"""Het energieplan uitvoeren in een echte Home Assistant, met een nagebootste broker en batterij."""

import copy
import json
import pathlib
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
VOORBEELD = json.loads((pathlib.Path(__file__).parent / "plan_voorbeeld.json").read_text(encoding="utf-8"))


def plan_vanaf_nu() -> bytes:
    """Het voorbeeldplan van de server, verschoven zodat het eerste kwartier nu loopt."""
    bericht = copy.deepcopy(VOORBEELD)
    verschuiving = int(time.time()) // 900 * 900 - bericht["t0"]
    for veld in ("gemaakt", "geldigTot", "t0"):
        bericht[veld] += verschuiving
    return json.dumps(bericht).encode()


@pytest.fixture
async def hub(hass):
    hass.states.async_set("sensor.p1", "300", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.bat", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    hass.states.async_set("number.batterij", "0", {"unit_of_measurement": "W"})

    verbinding = MagicMock(verbonden=True, async_stop=AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=ADRES.lower(), options={
        "net": "sensor.p1", "batterij": "sensor.bat", "soc": "sensor.soc", "bediening": True,
        "ems_aan": True, "ems_vermogen": "number.batterij"})
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=verbinding):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    hub = entry.runtime_data
    hub.verstuurd = lambda blad: [json.loads(c.args[1]) for c in verbinding.publiceer.call_args_list
                                  if c.args[0] == protocol.huis_topic(ADRES, blad)]
    yield hub
    await hass.config_entries.async_unload(entry.entry_id)


async def test_voert_het_plan_uit_via_de_number_entiteit(hass, hub):
    aanroepen = async_mock_service(hass, "number", "set_value")
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan_vanaf_nu())
    assert hub.ems.plan is not None

    await hub.ems.tik(300, 0, 40)

    assert aanroepen and aanroepen[-1].data == {"entity_id": "number.batterij", "value": -3000}
    status = hub.verstuurd("hub/planstatus")[-1]
    assert status["stand"] == "netladen" and status["bron"] == "plan" and status["planId"] == VOORBEELD["planId"]
    vermogens = hub.verstuurd("hub/vermogens")[-1]
    assert vermogens["uitvoerder"] == "homeassistant" and vermogens["niveau"] in ("A", "B") and "netladen" in vermogens["standen"]


async def test_pauze_geeft_de_batterij_zijn_eigen_stand_terug(hass, hub):
    aanroepen = async_mock_service(hass, "number", "set_value")
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan_vanaf_nu())
    await hub.ems.tik(300, 0, 40)

    hub.ems.pauzeer(3600)
    await hub.ems.tik(300, 0, 40)

    assert aanroepen[-1].data["value"] == 0
    assert not hub.ems.gestart
    assert hub.verstuurd("hub/planstatus")[-1]["bron"] == "terugval"


async def test_weigert_een_kapot_plan_en_houdt_het_vorige(hass, hub):
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan_vanaf_nu())
    kapot = json.loads(plan_vanaf_nu())
    kapot["stap"] = 600
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), json.dumps(kapot).encode())

    assert hub.ems.plan is not None and hub.ems.geweigerd == "stap moet 900 zijn"
    await hub.ems.tik(300, 0, 40)
    assert hub.verstuurd("hub/planstatus")[-1]["geweigerd"] == "stap moet 900 zijn"


async def test_leeg_plan_zet_het_energieplan_uit(hass, hub):
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan_vanaf_nu())
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), b"")
    assert hub.ems.plan is None


async def test_zonder_bediening_alleen_advies(hass, hub):
    aanroepen = async_mock_service(hass, "number", "set_value")
    hass.config_entries.async_update_entry(hub.entry, options={**hub.opties, "bediening": False})
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan_vanaf_nu())

    await hub.ems.tik(300, 0, 40)

    assert not aanroepen
    assert hub.ems.vermogens()["niveau"] == "D"


async def test_sensoren_van_het_plan(hass, hub):
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan_vanaf_nu())
    await hub.ems.tik(300, 0, 40)
    hub._bijgewerkt()
    await hass.async_block_till_done()

    register = er.async_get(hass)
    plan = hass.states.get(register.async_get_entity_id("sensor", DOMAIN, f"{hub.hub_id}_energieplan"))
    assert plan.state == "Goedkoop bijladen van het net"
    assert plan.attributes["blokken"][0]["stand"] == "netladen" and plan.attributes["kwartieren"]
    besparing = hass.states.get(register.async_get_entity_id("sensor", DOMAIN, f"{hub.hub_id}_verwachte_besparing"))
    assert float(besparing.state) == VOORBEELD["verwacht"]["besparingBatterij"]
