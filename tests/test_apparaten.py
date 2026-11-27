"""Apparaten veilig sturen in een echte Home Assistant: warmtepomp, boiler en vaatwasser op een stekker."""

import json
import pathlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.zonneburen import apparaat_regels, protocol
from custom_components.zonneburen.const import DOMAIN

ADRES = "De Kreekrug 9"
DATA = {"server": "https://zonopzwaagdijk.nl", "adres": ADRES, "apparaat_id": "ha-0123456789abcdef",
        "mqtt_host": "zonopzwaagdijk.nl", "mqtt_poort": 8883, "mqtt_gebruiker": "huis-abc", "mqtt_wachtwoord": "geheim"}
WP = {"enabled": True, "name": "Warmtepomp", "role": 2, "type": 6, "host": "climate.warmtepomp", "watts": 1500}
BOILER = {"enabled": True, "name": "Boiler", "role": 0, "type": 6, "host": "water_heater.boiler", "watts": 2000}
VAAT = {"enabled": True, "name": "Vaatwasser", "role": 3, "type": 6, "host": "switch.vaatwasser", "watts": 1800,
        "powerEntity": "sensor.vaatwasser_vermogen"}
SOORTEN = [{"i": 0, "rust": "normaal", "soort": "wp"}, {"i": 1, "rust": "uit", "soort": "boiler"},
           {"i": 2, "rust": "normaal", "soort": "witgoed", "duurMin": 120}]


def zet_wp(hass, temp=20.5, stand="heat"):
    hass.states.async_set("climate.warmtepomp", stand, {"temperature": temp, "current_temperature": 20, "min_temp": 14, "max_temp": 24,
                                                       "target_temp_step": 0.5, "hvac_modes": ["off", "heat", "cool", "heat_cool"]})


def zet_boiler(hass, temp=40, huidig=35):
    hass.states.async_set("water_heater.boiler", "heat_pump", {"temperature": temp, "current_temperature": huidig, "min_temp": 30,
                                                              "max_temp": 60, "operation_list": ["off", "heat_pump", "performance"]})


def zet_vaat(hass, aan=True, watt=0):
    hass.states.async_set("switch.vaatwasser", "on" if aan else "off")
    hass.states.async_set("sensor.vaatwasser_vermogen", str(watt), {"unit_of_measurement": "W"})


@pytest.fixture
async def hub(hass):
    hass.states.async_set("sensor.p1", "300", {"unit_of_measurement": "W"})
    zet_wp(hass)
    zet_boiler(hass)
    zet_vaat(hass)
    verbinding = MagicMock(verbonden=True, async_stop=AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=ADRES.lower(),
                            options={"net": "sensor.p1", "bediening": True, "ems_aan": True, "apparaten": [WP, BOILER, VAAT]})
    entry.add_to_hass(hass)
    with patch("custom_components.zonneburen.hub.Verbinding", return_value=verbinding):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry.runtime_data
    await hass.config_entries.async_unload(entry.entry_id)


class Diensten:
    def __init__(self, hass):
        self.lijsten = {(d, s): async_mock_service(hass, d, s) for d, s in (
            ("climate", "set_temperature"), ("climate", "set_hvac_mode"), ("climate", "turn_off"), ("climate", "turn_on"),
            ("water_heater", "set_temperature"), ("water_heater", "set_operation_mode"), ("water_heater", "turn_off"),
            ("homeassistant", "turn_on"), ("homeassistant", "turn_off"))}

    def __call__(self, domein, dienst):
        return self.lijsten[(domein, dienst)]

    def uit(self):
        """Alles wat een apparaat uitzet."""
        return (self("climate", "turn_off") + self("climate", "set_hvac_mode") + self("water_heater", "turn_off")
                + self("water_heater", "set_operation_mode")
                + [c for c in self("homeassistant", "turn_off") if c.data["entity_id"] != "switch.vaatwasser"])


def plan(lasten, apparaten=SOORTEN):
    voorbeeld = json.loads((pathlib.Path(__file__).parent / "plan_voorbeeld.json").read_text(encoding="utf-8"))
    verschuiving = int(time.time()) // 900 * 900 - voorbeeld["t0"]
    for veld in ("gemaakt", "geldigTot", "t0"):
        voorbeeld[veld] += verschuiving
    t0 = voorbeeld["t0"]
    voorbeeld["planId"] = f"p{time.monotonic_ns()}"
    voorbeeld["lasten"] = [{**l, "van": t0 + l["van"], "tot": t0 + l["tot"]} for l in lasten]
    voorbeeld["apparaten"] = list(apparaten)
    return json.dumps(voorbeeld).encode()


def stuur(hub, lasten, apparaten=SOORTEN):
    hub._verwerk(protocol.huis_topic(ADRES, "hub/plan"), plan(lasten, apparaten))


def later(hub, seconden=3600):
    """Tijd laten verstrijken voor de schrijflimiet, de naijltijd en de stilstand."""
    hub.sturing.limiet.geschreven = {e: (op - seconden, w) for e, (op, w) in hub.sturing.limiet.geschreven.items()}
    for s in hub.sturing.stil.values():
        s.actief_op = s.actief_op - seconden if s.actief_op is not None else None
        s.loopt_op = s.loopt_op - seconden if s.loopt_op is not None else None


async def ronde(hass, hub, d=None):
    await hub._stuur_slim()
    await hass.async_block_till_done()
    if d:  # het apparaat neemt de temperatuur over, zoals een echte thermostaat
        for (domein, _), lijst in d.lijsten.items():
            if lijst and domein in ("climate", "water_heater") and lijst[-1].service == "set_temperature":
                e = lijst[-1].data["entity_id"]
                (zet_wp if e.startswith("climate") else zet_boiler)(hass, lijst[-1].data["temperature"])


def laatste_temp(d, domein, entity_id):
    lijst = [c for c in d(domein, "set_temperature") if c.data["entity_id"] == entity_id]
    return lijst[-1].data["temperature"] if lijst else None


VOORVERWARMEN = {"id": "wp", "stand": "aanraden", "van": 0, "tot": 1800, "apparaat": 0, "actie": "aan", "temp": 21.5}
PAUZE = {"id": "wp", "stand": "blokkeren", "van": 0, "tot": 1800, "apparaat": 0, "actie": "aan", "temp": 17}


async def test_warmtepomp_gaat_in_geen_enkel_scenario_uit(hass, hub):
    d = Diensten(hass)
    for lasten in ([VOORVERWARMEN], [PAUZE], [], [{**PAUZE, "actie": "uit", "temp": None}]):
        stuur(hub, lasten)
        later(hub)
        await ronde(hass, hub, d)
    hub.noodstop = True
    later(hub)
    await ronde(hass, hub, d)
    hub.noodstop = False
    for modus in ("off", "auto", "on"):
        await hub._zet_stand(0, modus)
        later(hub)
        await ronde(hass, hub, d)
    assert d.uit() == []


async def test_voorverwarmen_normaal_besparen(hass, hub):
    d = Diensten(hass)
    stuur(hub, [VOORVERWARMEN])
    await ronde(hass, hub, d)
    assert laatste_temp(d, "climate", "climate.warmtepomp") == 21.5
    stuur(hub, [PAUZE])                       # de server vraagt 17 graden; de uitvoerder houdt het bij 20,0
    later(hub)
    await ronde(hass, hub, d)
    assert laatste_temp(d, "climate", "climate.warmtepomp") == 20.0
    stuur(hub, [])
    later(hub)
    await ronde(hass, hub, d)
    assert laatste_temp(d, "climate", "climate.warmtepomp") == 20.5


async def test_handmatig_veranderd_wordt_de_nieuwe_basis_en_vier_uur_pauze(hass, hub):
    d = Diensten(hass)
    stuur(hub, [VOORVERWARMEN])
    await ronde(hass, hub, d)
    zet_wp(hass, 21.0)                        # de bewoner zet hem zelf op 21
    later(hub)
    aantal = len(d("climate", "set_temperature"))
    await ronde(hass, hub, d)
    assert hub.sturing.basis["climate.warmtepomp"] == 21.0
    assert hub.sturing.pauze_tot["climate.warmtepomp"] > time.time() + 3.9 * 3600
    assert len(d("climate", "set_temperature")) == aantal
    assert hub.sturing.status[0].startswith("Jij stuurt")


async def test_een_cloud_krijgt_hooguit_een_schrijfactie_per_half_uur(hass, hub):
    d = Diensten(hass)
    hub.sturing._cloud["climate.warmtepomp"] = True
    stuur(hub, [VOORVERWARMEN])
    await ronde(hass, hub, d)
    stuur(hub, [PAUZE])                       # tien minuten later een ander plan
    later(hub, 600)
    await ronde(hass, hub, d)
    assert [c.data["temperature"] for c in d("climate", "set_temperature")] == [21.5]


async def test_weinig_aanroepen_over_bij_de_cloud_dan_stoppen(hass, hub):
    d = Diensten(hass)
    hub.sturing._cloud["climate.warmtepomp"] = True
    hub.sturing.ratelimit_over = lambda _e: 20
    stuur(hub, [VOORVERWARMEN])
    await ronde(hass, hub, d)
    assert d("climate", "set_temperature") == []


async def test_boiler_warmer_nooit_uit_en_niet_als_hij_al_warm_is(hass, hub):
    d = Diensten(hass)
    opwarmen = {"id": "boiler", "stand": "aanraden", "van": 0, "tot": 1800, "apparaat": 1, "actie": "aan"}
    stuur(hub, [opwarmen])
    await ronde(hass, hub, d)
    assert laatste_temp(d, "water_heater", "water_heater.boiler") == 50
    stuur(hub, [])                            # rust "uit" voor een boiler: gewoon terug naar 40
    later(hub)
    await ronde(hass, hub, d)
    assert laatste_temp(d, "water_heater", "water_heater.boiler") == 40
    assert d.uit() == []

    zet_boiler(hass, 40, huidig=48)           # al bijna op temperatuur: geen opwarmblok
    stuur(hub, [opwarmen])
    later(hub)
    aantal = len(d("water_heater", "set_temperature"))
    await ronde(hass, hub, d)
    assert len(d("water_heater", "set_temperature")) == aantal


async def test_vaatwasser_gaat_niet_uit_terwijl_hij_draait_en_blijft_aan_na_afloop(hass, hub):
    d = Diensten(hass)
    zet_vaat(hass, aan=True, watt=1800)
    stuur(hub, [])                            # geen blok: niets doen
    await ronde(hass, hub)
    assert d("homeassistant", "turn_off") == []

    # Slim starten: de bewoner drukte op start; de stekker gaat uit tot het blok.
    await hub.klaarzetten(2, time.time() + 8 * 3600)
    assert d("homeassistant", "turn_off")[-1].data["entity_id"] == "switch.vaatwasser"
    zet_vaat(hass, aan=False, watt=0)
    await ronde(hass, hub)
    assert len(d("homeassistant", "turn_off")) == 1

    # Het blok begint: aan.
    stuur(hub, [{"id": "witgoed", "stand": "start", "van": 0, "tot": 1800, "apparaat": 2, "actie": "aan"}])
    await ronde(hass, hub)
    assert d("homeassistant", "turn_on")[-1].data["entity_id"] == "switch.vaatwasser"
    zet_vaat(hass, aan=True, watt=1800)
    await ronde(hass, hub)

    # Het blok is voorbij, het programma loopt nog: niet uit.
    stuur(hub, [])
    later(hub, 1800)
    await ronde(hass, hub)
    assert len(d("homeassistant", "turn_off")) == 1
    # Klaar (vijf minuten stil): en daarna gewoon aan.
    zet_vaat(hass, aan=True, watt=1)
    await ronde(hass, hub)
    later(hub, apparaat_regels.KLAAR_S + 10)
    await ronde(hass, hub)
    assert 2 not in hub.sturing.loopt_door
    assert len(d("homeassistant", "turn_off")) == 1
    # De server hoort het ook: die plant het startmoment, samen met de batterij en de wijk.
    assert any(c.args[0] == protocol.huis_topic(ADRES, "hub/klaarzetten") for c in hub.verbinding.publiceer.call_args_list)


async def test_verwijderen_zet_de_warmtepomp_terug_op_zijn_eigen_temperatuur(hass, hub):
    d = Diensten(hass)
    stuur(hub, [VOORVERWARMEN])
    await ronde(hass, hub, d)
    assert laatste_temp(d, "climate", "climate.warmtepomp") == 21.5
    assert hub._zet_instellingen({"loads": [{**WP, "enabled": False}, BOILER, VAAT]}) is None
    await hass.async_block_till_done()
    assert laatste_temp(d, "climate", "climate.warmtepomp") == 20.5
    assert "climate.warmtepomp" not in hub.sturing.basis


async def test_terug_naar_slim_regelen_zet_niets_om_tot_het_volgende_blok(hass, hub):
    d = Diensten(hass)
    hass.states.async_set("switch.boiler", "on")
    gewone = {"enabled": True, "name": "Boiler", "role": 0, "type": 6, "host": "switch.boiler", "watts": 2000}
    hass.config_entries.async_update_entry(hub.entry, options={**hub.entry.options, "apparaten": [gewone]})
    stuur(hub, [{"id": "boiler", "stand": "aan", "van": 3600, "tot": 5400, "apparaat": 0, "actie": "aan"}], [{"i": 0, "rust": "uit"}])
    await hub._zet_stand(0, "on")
    await hub._zet_stand(0, "auto")
    await hub._pas_toe()
    await hass.async_block_till_done()
    assert [c for c in d("homeassistant", "turn_off") if c.data["entity_id"] == "switch.boiler"] == []
