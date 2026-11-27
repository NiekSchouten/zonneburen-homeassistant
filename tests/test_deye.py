"""Een Deye of Sunsynk sturen via zijn tijdprogramma's, in een echte Home Assistant met nagebootste entiteiten."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.zonneburen import protocol
from custom_components.zonneburen.const import DOMAIN

ADRES = "De Kreekrug 9"
DATA = {"server": "https://zonopzwaagdijk.nl", "adres": ADRES, "apparaat_id": "ha-0123456789abcdef",
        "mqtt_host": "zonopzwaagdijk.nl", "mqtt_poort": 8883, "mqtt_gebruiker": "huis-abc", "mqtt_wachtwoord": "geheim"}
LADEN = [f"select.deye_program_{n}_charging" for n in range(1, 7)]
SOC = [f"number.deye_program_{n}_soc" for n in range(1, 7)]
TIJD = [f"time.deye_program_{n}_time" for n in range(1, 7)]
OPTIES = {"net": "sensor.p1", "bediening": True, "ems_aan": True, "ems_profiel": "deye_tou",
          "ems_tou": "select.deye_time_of_use", "ems_tou_laden": LADEN, "ems_tou_soc": SOC, "ems_tou_tijd": TIJD,
          "ems_tou_stroom": "number.deye_battery_grid_charging_current", "ems_tou_spanning": "sensor.deye_battery_voltage",
          "ems_tou_max_pct": 90, "ems_tou_min_pct": 20}


def zet_deye(hass, laden="Disabled", soc=20, stroom=50, tou="Disabled", tijden=None):
    hass.states.async_set("sensor.p1", "500", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.deye_battery_voltage", "51", {"unit_of_measurement": "V"})
    hass.states.async_set("select.deye_time_of_use", tou, {"options": ["Disabled", "Enabled", "Monday"]})
    hass.states.async_set("number.deye_battery_grid_charging_current", str(stroom), {"min": 0, "max": 240, "unit_of_measurement": "A"})
    for n in range(6):
        hass.states.async_set(LADEN[n], laden, {"options": ["Disabled", "Grid", "Gen", "Grid & Gen"]})
        hass.states.async_set(SOC[n], str(soc), {"min": 0, "max": 100, "unit_of_measurement": "%"})
        hass.states.async_set(TIJD[n], (tijden or ["01:30:00"] * 6)[n])


@pytest.fixture
async def hub(hass):
    zet_deye(hass)
    verbinding = MagicMock(verbonden=True, async_stop=AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=ADRES.lower(), options=OPTIES, version=1, minor_version=2)
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=verbinding):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    hub = entry.runtime_data
    yield hub
    await hass.config_entries.async_unload(entry.entry_id)


def plan(stand, bat_w, soc_eind):
    t0 = int(time.time()) // 900 * 900
    rijen = [[stand, 1000 if stand == "netladen" else None, bat_w, soc_eind, 0.2, 0.1]] * 8
    return json.dumps({"v": 1, "planId": f"p-{stand}", "gemaakt": t0 - 60, "geldigTot": t0 + 8 * 900, "t0": t0, "stap": 900,
                       "grenzen": {"maxLaadW": 6000, "maxOntlaadW": 6000}, "s": rijen}).encode()


def diensten(hass):
    return {"select": async_mock_service(hass, "select", "select_option"), "number": async_mock_service(hass, "number", "set_value"),
            "time": async_mock_service(hass, "time", "set_value")}


async def test_laden_met_5000_watt_tot_80_procent(hass, hub):
    d = diensten(hass)
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan("netladen", -5000, 80))
    await hub.ems.tik(500, 0, 47)
    await hass.async_block_till_done()

    gekozen = {c.data["entity_id"]: c.data["option"] for c in d["select"]}
    assert all(gekozen[i] == "Grid" for i in LADEN)
    assert gekozen["select.deye_time_of_use"] == "Enabled"
    gezet = {c.data["entity_id"]: c.data["value"] for c in d["number"]}
    assert all(gezet[i] == 80 for i in SOC)
    assert gezet["number.deye_battery_grid_charging_current"] == 98     # 5000 W / 51 V
    assert [c.data["time"] for c in d["time"]] == ["00:00:00", "04:00:00", "08:00:00", "12:00:00", "16:00:00", "20:00:00"]

    staat = next(s for s in hass.states.async_all("sensor") if s.attributes.get("zonneburen") == "energieplan")
    assert staat.attributes["toegepaste_stand"] == "netladen" and staat.attributes["gevraagd_vermogen_w"] == -5000
    assert staat.attributes["laatste_opdracht"]["resultaat"].endswith("gezet")
    # De server hoort dat deze omvormer niet met een vast vermogen kan ontladen.
    assert "netontladen" not in hub.ems.vermogens()["standen"] and hub.ems.vermogens()["niveau"] == "B"


async def test_vasthouden_op_de_lading_van_nu(hass, hub):
    zet_deye(hass, laden="Grid", soc=80, tou="Enabled", tijden=["00:00:00", "04:00:00", "08:00:00", "12:00:00", "16:00:00", "20:00:00"])
    d = diensten(hass)
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan("vasthouden", 0, 47))
    await hub.ems.tik(500, 0, 47)
    await hass.async_block_till_done()

    assert {c.data["option"] for c in d["select"]} == {"Disabled"} and len(d["select"]) == 6
    assert {c.data["value"] for c in d["number"]} == {47}
    assert not d["time"]


async def test_zelfverbruik_naar_de_ondergrens_en_niets_schrijven_als_het_al_klopt(hass, hub):
    zet_deye(hass, laden="Disabled", soc=20, tou="Enabled", tijden=["00:00:00", "04:00:00", "08:00:00", "12:00:00", "16:00:00", "20:00:00"])
    d = diensten(hass)
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan("zelfverbruik", 800, 40))
    await hub.ems.tik(500, 800, 60)
    await hass.async_block_till_done()
    assert not d["select"] and not d["number"] and not d["time"]
    assert hub.ems.deye.laatste_opdracht["resultaat"] == "niets te veranderen"


async def test_sturing_uit_zet_de_programmas_netjes_terug(hass, hub):
    d = diensten(hass)
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan("netladen", -5000, 80))
    await hub.ems.tik(500, 0, 47)
    zet_deye(hass, laden="Grid", soc=80, tou="Enabled")
    d = diensten(hass)
    hass.config_entries.async_update_entry(hub.entry, options={**hub.entry.options, "ems_aan": False})
    await hub.ems.tik(500, 0, 47)
    await hass.async_block_till_done()

    assert {c.data["option"] for c in d["select"] if c.data["entity_id"] in LADEN} == {"Disabled"}
    assert {c.data["value"] for c in d["number"] if c.data["entity_id"] in SOC} == {20}
    assert not hub.ems.gestart


async def test_noodstop_laadt_niet_meer_van_het_net(hass, hub):
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan("netladen", -5000, 80))
    await hub.ems.tik(500, 0, 47)
    zet_deye(hass, laden="Grid", soc=80, tou="Enabled")
    d = diensten(hass)
    hub.noodstop = True
    await hub.ems.tik(500, 0, 47)
    await hass.async_block_till_done()
    assert {c.data["option"] for c in d["select"] if c.data["entity_id"] in LADEN} == {"Disabled"}
    assert {c.data["value"] for c in d["number"] if c.data["entity_id"] in SOC} == {20}


async def test_oude_config_wordt_gemigreerd_en_de_teruglevergrens_vervalt(hass):
    hass.states.async_set("sensor.p1", "500", {"unit_of_measurement": "W"})
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="oud", version=1, minor_version=1,
                            options={"net": "sensor.p1", "ems_aan": True, "ems_vermogen": "number.deye_zero_export_power"})
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=MagicMock(verbonden=True, async_stop=AsyncMock())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.minor_version == 2 and entry.options["ems_vermogen"] is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_een_vermogen_uit_een_oude_config_blijft_het_profiel_vermogen(hass):
    hass.states.async_set("sensor.p1", "500", {"unit_of_measurement": "W"})
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="oud2", version=1, minor_version=1,
                            options={"net": "sensor.p1", "ems_aan": True, "ems_vermogen": "number.accu_setpoint"})
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=MagicMock(verbonden=True, async_stop=AsyncMock())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.options["ems_profiel"] == "vermogen" and entry.options["ems_vermogen"] == "number.accu_setpoint"
    await hass.config_entries.async_unload(entry.entry_id)
