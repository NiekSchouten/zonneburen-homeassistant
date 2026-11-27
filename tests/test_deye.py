"""Een Deye of Sunsynk sturen, in een echte Home Assistant met een nagebootste Deye (51 V)."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.zonneburen import protocol
from custom_components.zonneburen.const import DOMAIN

ADRES = "De Kreekrug 9"
DATA = {"server": "https://zonopzwaagdijk.nl", "adres": ADRES, "apparaat_id": "ha-0123456789abcdef",
        "mqtt_host": "zonopzwaagdijk.nl", "mqtt_poort": 8883, "mqtt_gebruiker": "huis-abc", "mqtt_wachtwoord": "geheim"}
P = "omvormer_kelder"
LADEN = [f"select.{P}_program_{n}_charging" for n in range(1, 7)]
SOC = [f"number.{P}_program_{n}_soc" for n in range(1, 7)]
VERMOGEN = [f"number.{P}_program_{n}_power" for n in range(1, 7)]
TIJD = [f"time.{P}_program_{n}_time" for n in range(1, 7)]
WERK, PATROON, TOU = f"select.{P}_work_mode", f"select.{P}_energy_pattern", f"select.{P}_time_of_use"
STROOM, MAX_LAAD, MAX_ONTLAAD = f"number.{P}_battery_grid_charging_current", f"number.{P}_battery_max_charging_current", \
    f"number.{P}_battery_max_discharging_current"
NETLADEN, EXPORT = f"switch.{P}_battery_grid_charging", f"number.{P}_grid_max_export_power"
CT, EXPORT_FIRST = "Zero Export To CT", "Export First"
ONZE_TIJDEN = ["00:00:00", "04:00:00", "08:00:00", "12:00:00", "16:00:00", "20:00:00"]
OPTIES = {"net": "sensor.p1", "bediening": True, "ems_aan": True, "ems_profiel": "deye_tou",
          "ems_tou": TOU, "ems_tou_laden": LADEN, "ems_tou_soc": SOC, "ems_tou_tijd": TIJD, "ems_tou_vermogen": VERMOGEN,
          "ems_tou_stroom": STROOM, "ems_tou_spanning": f"sensor.{P}_battery_voltage", "ems_tou_werkmodus": WERK,
          "ems_tou_patroon": PATROON, "ems_tou_max_laad_a": MAX_LAAD, "ems_tou_max_ontlaad_a": MAX_ONTLAAD,
          "ems_tou_netladen": NETLADEN, "ems_tou_max_export": EXPORT, "ems_tou_max_pct": 90, "ems_tou_min_pct": 20}


def zet_deye(hass, laden="Disabled", soc=20, vermogen=8000, stroom=50, max_laad=200, max_ontlaad=200, werk=CT, tou="Disabled",
             tijden=None, netladen="on", export=12000):
    """De toestand van de omvormer. Standaard: zoals de bewoner hem zelf had."""
    hass.states.async_set("sensor.p1", "500", {"unit_of_measurement": "W"})
    hass.states.async_set(f"sensor.{P}_battery_voltage", "51", {"unit_of_measurement": "V"})
    hass.states.async_set(WERK, werk, {"options": [EXPORT_FIRST, "Zero Export To Load", CT]})
    hass.states.async_set(PATROON, "Battery First", {"options": ["Battery First", "Load First"]})
    hass.states.async_set(TOU, tou, {"options": ["Disabled", "Enabled", "Monday"]})
    hass.states.async_set(NETLADEN, netladen)
    for entity_id, waarde, hoog in ((STROOM, stroom, 350), (MAX_LAAD, max_laad, 350), (MAX_ONTLAAD, max_ontlaad, 350), (EXPORT, export, 80000)):
        hass.states.async_set(entity_id, str(waarde), {"min": 0, "max": hoog})
    for n in range(6):
        hass.states.async_set(LADEN[n], laden, {"options": ["Disabled", "Grid", "Gen", "Both"]})
        hass.states.async_set(SOC[n], str(soc), {"min": 0, "max": 100, "unit_of_measurement": "%"})
        hass.states.async_set(VERMOGEN[n], str(vermogen), {"min": 0, "max": 32000, "unit_of_measurement": "W"})
        hass.states.async_set(TIJD[n], (tijden or ["01:30:00"] * 6)[n])


def zoals_zelfverbruik(hass):
    """Alles al zoals Zonneburen het bij zelfverbruik wil: dan valt er niets te schrijven."""
    zet_deye(hass, laden="Disabled", soc=20, vermogen=10000, max_ontlaad=196, tou="Enabled", tijden=ONZE_TIJDEN)


async def maak(hass, opties=OPTIES):
    verbinding = MagicMock(verbonden=True, async_stop=AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=ADRES.lower(), options=opties, version=1, minor_version=3)
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=verbinding):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry.runtime_data


@pytest.fixture
async def hub(hass):
    zet_deye(hass)
    hub = await maak(hass)
    yield hub
    await hass.config_entries.async_unload(hub.entry.entry_id)


def plan(stand, bat_w, soc_eind):
    t0 = int(time.time()) // 900 * 900
    rijen = [[stand, 1000 if stand in ("netladen", "netontladen") else None, bat_w, soc_eind, 0.2, 0.1]] * 8
    return json.dumps({"v": 1, "planId": f"p-{stand}-{bat_w}", "gemaakt": t0 - 60, "geldigTot": t0 + 8 * 900, "t0": t0, "stap": 900,
                       "grenzen": {"maxLaadW": 10000, "maxOntlaadW": 10000}, "s": rijen}).encode()


class Diensten:
    """Alle schrijfacties naar de omvormer, per entiteit de laatste waarde."""

    def __init__(self, hass):
        self.oproepen = []
        for domein, dienst in (("select", "select_option"), ("number", "set_value"), ("time", "set_value"),
                               ("switch", "turn_on"), ("switch", "turn_off")):
            self.oproepen.append((dienst, async_mock_service(hass, domein, dienst)))

    @property
    def alle(self):
        return [c for _, lijst in self.oproepen for c in lijst]

    def waarde(self, entity_id):
        for dienst, lijst in self.oproepen:
            for c in lijst:
                if c.data["entity_id"] == entity_id:
                    return c.data.get("option", c.data.get("value", c.data.get("time", "on" if dienst == "turn_on" else "off")))
        return None


async def stuur(hass, hub, stand, bat_w, soc_eind, soc_nu=47):
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan(stand, bat_w, soc_eind))
    await hub.ems.tik(500, 0, soc_nu)
    await hass.async_block_till_done()


async def test_laden_5000_watt_tot_80_procent(hass, hub):
    d = Diensten(hass)
    await stuur(hass, hub, "netladen", -5000, 80)
    assert all(d.waarde(i) == "Grid" for i in LADEN) and all(d.waarde(i) == 80 for i in SOC)
    assert all(d.waarde(i) == 5000 for i in VERMOGEN)
    assert d.waarde(STROOM) == 98                       # 5000 W / 51 V
    assert d.waarde(TOU) == "Enabled" and d.waarde(WERK) is None   # stond al op Zero Export To CT
    assert [d.waarde(i) for i in TIJD] == ONZE_TIJDEN
    assert d.waarde(MAX_LAAD) is None and d.waarde(MAX_ONTLAAD) is None   # eigen waarden blijven staan
    staat = next(s for s in hass.states.async_all("sensor") if s.attributes.get("zonneburen") == "energieplan")
    assert staat.attributes["toegepaste_stand"] == "netladen" and staat.attributes["gevraagd_vermogen_w"] == -5000
    v = hub.ems.vermogens()
    assert v["niveau"] == "A" and v["regeltZelfOpMeter"] and v["minSoc"] == 20 and v["maxSocNet"] == 90
    assert "netontladen" not in v["standen"]


async def test_zelfverbruik_met_laden_uit_de_zon_wordt_nooit_grid(hass, hub):
    zet_deye(hass, laden="Grid", soc=80, stroom=98, max_ontlaad=100, tou="Enabled", tijden=ONZE_TIJDEN)
    d = Diensten(hass)
    await stuur(hass, hub, "zelfverbruik", -1500, 60)
    assert {d.waarde(i) for i in LADEN} == {"Disabled"} and {d.waarde(i) for i in SOC} == {20}
    assert d.waarde(STROOM) is None                      # laadstroom van het net niet aangeraakt
    assert d.waarde(MAX_ONTLAAD) == 196 and {d.waarde(i) for i in VERMOGEN} == {10000}


async def test_zelfverbruik_dat_rond_nul_schommelt_schrijft_niets(hass, hub):
    zoals_zelfverbruik(hass)
    d = Diensten(hass)
    for bat_w in (-250, 250, -250, 250):
        hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan("zelfverbruik", bat_w, 50))
        hub.ems.deye._volgende = 0
        await hub.ems.tik(bat_w, bat_w, 47)
    await hass.async_block_till_done()
    assert not d.alle


async def test_terugleveren_zonder_toestemming_is_ontladen_voor_het_huis(hass, hub):
    zet_deye(hass, laden="Grid", soc=80)
    d = Diensten(hass)
    await stuur(hass, hub, "netontladen", 2000, 30, soc_nu=60)
    assert {d.waarde(i) for i in LADEN} == {"Disabled"} and {d.waarde(i) for i in SOC} == {20}
    assert d.waarde(MAX_ONTLAAD) == 39 and {d.waarde(i) for i in VERMOGEN} == {2000}
    assert d.waarde(WERK) is None and d.waarde(EXPORT) is None   # geen Export First


async def test_terugleveren_met_toestemming_en_daarna_weer_ct(hass):
    zet_deye(hass)
    hub = await maak(hass, {**OPTIES, "ems_tou_terugleveren": True})
    d = Diensten(hass)
    await stuur(hass, hub, "netontladen", 3000, 30, soc_nu=60)
    assert d.waarde(WERK) == EXPORT_FIRST and d.waarde(EXPORT) == 3000 and d.waarde(MAX_ONTLAAD) == 59
    assert "netontladen" in hub.ems.vermogens()["standen"]

    hass.states.async_set(WERK, EXPORT_FIRST, {"options": [EXPORT_FIRST, "Zero Export To Load", CT]})
    hass.states.async_set(EXPORT, "3000", {"min": 0, "max": 80000})
    d = Diensten(hass)
    await stuur(hass, hub, "zelfverbruik", 500, 30, soc_nu=50)
    assert d.waarde(WERK) == CT and d.waarde(EXPORT) == 12000      # eigen maximum terug
    await hass.config_entries.async_unload(hub.entry.entry_id)


async def test_vasthouden_op_de_lading_van_nu(hass, hub):
    zet_deye(hass, laden="Grid", soc=80)
    d = Diensten(hass)
    await stuur(hass, hub, "vasthouden", 0, 47, soc_nu=47)
    assert {d.waarde(i) for i in LADEN} == {"Disabled"} and {d.waarde(i) for i in SOC} == {47}


async def test_standby_zet_de_stromen_op_nul_en_daarna_terug(hass, hub):
    d = Diensten(hass)
    await stuur(hass, hub, "standby", 0, 47)
    assert d.waarde(MAX_LAAD) == 0 and d.waarde(MAX_ONTLAAD) == 0 and {d.waarde(i) for i in VERMOGEN} == {0}

    zet_deye(hass, max_laad=0, max_ontlaad=0, vermogen=0, tou="Enabled", tijden=ONZE_TIJDEN)
    d = Diensten(hass)
    hub.noodstop = True
    await hub.ems.tik(500, 0, 47)
    await hass.async_block_till_done()
    assert d.waarde(MAX_LAAD) == 200 and d.waarde(MAX_ONTLAAD) == 200 and {d.waarde(i) for i in VERMOGEN} == {8000}


async def test_sturing_uit_zet_precies_de_eigen_instellingen_terug(hass, hub):
    await stuur(hass, hub, "netladen", -5000, 80)
    zet_deye(hass, laden="Grid", soc=80, vermogen=5000, stroom=98, tou="Enabled", tijden=ONZE_TIJDEN)
    d = Diensten(hass)
    hass.config_entries.async_update_entry(hub.entry, options={**hub.entry.options, "ems_aan": False})
    await hub.ems.tik(500, 0, 47)
    await hass.async_block_till_done()

    assert {d.waarde(i) for i in LADEN} == {"Disabled"} and {d.waarde(i) for i in SOC} == {20}
    assert {d.waarde(i) for i in VERMOGEN} == {8000} and d.waarde(STROOM) == 50 and d.waarde(TOU) == "Disabled"
    assert [d.waarde(i) for i in TIJD] == ["01:30:00"] * 6
    assert not hub.ems.gestart and hub.ems.deye.opgeslagen == {}


async def test_noodstop_zet_de_eigen_instellingen_terug(hass, hub):
    await stuur(hass, hub, "netladen", -5000, 80)
    zet_deye(hass, laden="Grid", soc=80, vermogen=5000, stroom=98, tou="Enabled", tijden=ONZE_TIJDEN)
    d = Diensten(hass)
    hub.noodstop = True
    await hub.ems.tik(500, 0, 47)
    await hass.async_block_till_done()
    assert {d.waarde(i) for i in LADEN} == {"Disabled"} and {d.waarde(i) for i in VERMOGEN} == {8000}
    assert hub.ems.deye.opgeslagen       # bewaard: na de noodstop gaat het sturen verder


async def test_binnen_vijf_minuten_geen_tweede_schrijfactie_bij_dezelfde_stand(hass, hub):
    await stuur(hass, hub, "vasthouden", 0, 47, soc_nu=47)
    zet_deye(hass, soc=47, vermogen=10000, tou="Enabled", tijden=ONZE_TIJDEN)
    d = Diensten(hass)
    hub.ems.deye._volgende = 0
    await hub.ems.tik(500, 0, 55)          # 8 % voller, zelfde stand, binnen vijf minuten
    await hass.async_block_till_done()
    assert not [c for c in d.alle if c.data["entity_id"] in SOC]


async def test_bij_het_afsluiten_niets_terugzetten(hass, hub):
    await stuur(hass, hub, "netladen", -5000, 80)
    d = Diensten(hass)
    await hub.ems.async_stop(afsluiten=True)
    assert not d.alle and hub.ems.deye.opgeslagen


async def test_stond_zonneburen_al_te_sturen_dan_is_de_eigen_stand_veilig(hass):
    # Na 0.8: de programma's staan al op onze tijden en op Grid. Dat is niet de stand van de bewoner.
    zet_deye(hass, laden="Grid", soc=80, tou="Enabled", tijden=ONZE_TIJDEN)
    hub = await maak(hass)
    await stuur(hass, hub, "zelfverbruik", 0, 50)
    opgeslagen = hub.ems.deye.opgeslagen
    assert {opgeslagen[i] for i in LADEN} == {"Disabled"} and {opgeslagen[i] for i in SOC} == {"20"}
    await hass.config_entries.async_unload(hub.entry.entry_id)


async def test_oude_config_met_teruglevergrens_wordt_deye_als_die_er_is(hass):
    register = er.async_get(hass)
    for entity_id in (TOU, *LADEN, *SOC, *TIJD, *VERMOGEN, WERK, STROOM):
        domein, naam = entity_id.split(".")
        register.async_get_or_create(domein, "solarman", naam, suggested_object_id=naam)
    zet_deye(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="oud", version=1, minor_version=1,
                            options={"net": "sensor.p1", "ems_aan": True, "ems_vermogen": f"number.{P}_zero_export_power"})
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=MagicMock(verbonden=True, async_stop=AsyncMock())):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.minor_version == 3 and entry.options["ems_profiel"] == "deye_tou"
    assert entry.options["ems_tou"] == TOU and entry.options["ems_tou_laden"] == LADEN and entry.options["ems_tou_werkmodus"] == WERK
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
