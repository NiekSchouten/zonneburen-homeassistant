"""De hub in een echte Home Assistant, met een nagebootste broker."""

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
BOILER = {"enabled": True, "name": "Boiler", "role": 0, "type": 6, "host": "switch.boiler", "watts": 2000, "wijk": True}


@pytest.fixture
async def hub(hass):
    hass.states.async_set("sensor.p1", "-1500", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.zon", "2.5", {"unit_of_measurement": "kW"})
    hass.states.async_set("switch.boiler", "off", {"friendly_name": "Boiler"})
    hass.states.async_set("sensor.temperatuur", "20", {"unit_of_measurement": "°C"})

    verbinding = MagicMock(verbonden=True, async_stop=AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=ADRES.lower(),
                            options={"net": "sensor.p1", "zon": "sensor.zon", "bediening": True, "wijk_automatisch": True,
                                     "apparaten": [BOILER]})
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=verbinding):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    hub = entry.runtime_data
    hub.verstuurd = lambda blad: [json.loads(c.args[1]) for c in verbinding.publiceer.call_args_list
                                  if c.args[0] == protocol.huis_topic(ADRES, blad)]
    yield hub
    await hass.config_entries.async_unload(entry.entry_id)


def opdracht(actie, **velden):
    return json.dumps({"id": f"id-{actie}", "actie": actie, "ts": int(time.time()), **velden}).encode()


async def test_live_en_telemetrie_uit_de_sensoren(hass, hub):
    hub._tik()
    assert hub.live["grid"] == -1500 and hub.live["pv"] == 2500 and hub.live["load"] == 1000
    assert hub.verstuurd("hub/live")[-1]["grid"] == -1500
    info = hub.verstuurd("hub/info")[-1]
    assert info["fw"].startswith("ha-") and info["soort"] == "homeassistant" and info["apparaten"]["loads"][0]["name"] == "Boiler"

    hub._stuur_telemetrie()
    telemetrie = hub.verstuurd("telemetrie")[-1]
    assert telemetrie["hardware_token"] == ADRES
    assert telemetrie["teruglevering_watt"] == 1500 and telemetrie["verbruik_watt"] == 0


async def test_apparatenlijst_komt_direct_uit_home_assistant(hass, hub):
    hub._bij_bericht(protocol.huis_topic(ADRES, "hub/opdracht"), opdracht("gegevens", soort="ha"))
    await hass.async_block_till_done()
    antwoord = hub.verstuurd("hub/antwoord")[-1]
    entiteiten = [e["e"] for e in antwoord["data"]["entiteiten"]]
    assert antwoord["ok"] and "switch.boiler" in entiteiten and "sensor.p1" in entiteiten
    assert "sensor.temperatuur" not in entiteiten


async def test_webapp_schakelt_een_apparaat(hass, hub):
    aan = async_mock_service(hass, "homeassistant", "turn_on")
    hub._bij_bericht(protocol.huis_topic(ADRES, "hub/opdracht"), opdracht("modus", i=0, modus="on"))
    await hass.async_block_till_done()
    assert aan[0].data["entity_id"] == "switch.boiler"
    assert hub.verstuurd("hub/antwoord")[-1] == {"id": "id-modus", "ok": True}


async def test_wijkcommando_zet_de_boiler_aan_en_bevestigt(hass, hub):
    aan = async_mock_service(hass, "homeassistant", "turn_on")
    hub._bij_bericht(protocol.TOPIC_SCHAKELEN, json.dumps({"adres": ADRES, "commando": "surplus_start"}).encode())
    await hass.async_block_till_done()
    assert hub.wijk_actief and aan[0].data["entity_id"] == "switch.boiler"
    assert hub.verstuurd("ack")[-1]["resultaat"] == "uitgevoerd"

    hub._bij_bericht(protocol.TOPIC_SCHAKELEN, json.dumps({"adres": "Ander huis", "commando": "surplus_stop"}).encode())
    await hass.async_block_till_done()
    assert hub.wijk_actief


async def test_instellingen_uit_de_webapp(hass, hub):
    hub._bij_bericht(protocol.huis_topic(ADRES, "hub/opdracht"), opdracht("gegevens", soort="instellingen"))
    await hass.async_block_till_done()
    instellingen = hub.verstuurd("hub/antwoord")[-1]["data"]
    assert instellingen["source"] == "ha" and instellingen["haEntGrid"] == "sensor.p1" and len(instellingen["loads"]) == 32

    instellingen["loads"][1] = {**BOILER, "name": "Lamp", "host": "light.keuken", "role": 4}
    hub._bij_bericht(protocol.huis_topic(ADRES, "hub/opdracht"),
                     opdracht("instellingen", waarden={"loads": instellingen["loads"], "pvPeakW": 4000}))
    await hass.async_block_till_done()
    assert hub.verstuurd("hub/antwoord")[-1]["ok"]
    assert hub.entry.options["apparaten"][1]["host"] == "light.keuken"
    assert hub.entry.options["overig"]["pvPeakW"] == 4000

    hub._bij_bericht(protocol.huis_topic(ADRES, "hub/opdracht"), opdracht("instellingen", waarden={"smHost": "192.168.1.5"}))
    await hass.async_block_till_done()
    assert not hub.verstuurd("hub/antwoord")[-1]["ok"]


async def test_meldt_een_tweede_hub_op_hetzelfde_adres(hass, hub):
    hub._tik()
    hub._stuur_telemetrie()
    eigen = hub.verbinding.publiceer.call_args_list[-1].args[1]
    hub._bij_bericht(protocol.huis_topic(ADRES, "telemetrie"), eigen.encode())
    assert hub.andere_hub is None

    hub._bij_bericht(protocol.huis_topic(ADRES, "telemetrie"),
                     json.dumps({"hardware_token": ADRES, "hub_id": "a4cb8fd53618"}).encode())
    await hass.async_block_till_done()
    assert hub.andere_hub == "a4cb8fd53618"
    assert "Zonneburen Link" in hub.logboek[0]["m"]


async def test_historie_uit_home_assistant(hass, hub):
    nu = time.time()
    uren = [(nu - 86400 * 3, {"net": 400, "pv": 2000})]
    vijf = [(nu - 3600, {"net": -800, "pv": 1500})]
    with patch("custom_components.zonneburen.hub.historie.lees", side_effect=[uren, vijf]):
        await hub.vul_historie_aan()
    assert max(d["pv"] for d in hub.dagen.dagen.values()) == 2.0
    assert hub.vakken[0]["grid"] == -800 and hub.vakken[0]["load"] == 700

    hub._bij_bericht(protocol.huis_topic(ADRES, "hub/opdracht"), opdracht("gegevens", soort="uren", n=48))
    await hass.async_block_till_done()
    assert hub.verstuurd("hub/antwoord")[-1]["data"]["pv"][0] == 1500


async def test_zonder_bediening_alleen_lezen(hass, hub):
    hass.config_entries.async_update_entry(hub.entry, options={**hub.entry.options, "bediening": False})
    hub._bij_bericht(protocol.huis_topic(ADRES, "hub/opdracht"), opdracht("modus", i=0, modus="on"))
    await hass.async_block_till_done()
    assert not hub.verstuurd("hub/antwoord")[-1]["ok"]
