"""Het protocol moet precies spreken wat de server en de webapp van de hub verwachten."""

import importlib.util
import json
import pathlib
import sys
from datetime import date

import pytest

# Rechtstreeks laden: dit bestand heeft geen Home Assistant nodig, de rest van de integratie wel.
_pad = pathlib.Path(__file__).parents[1] / "custom_components" / "zonneburen" / "protocol.py"
_spec = importlib.util.spec_from_file_location("protocol", _pad)
p = importlib.util.module_from_spec(_spec)
sys.modules["protocol"] = p
_spec.loader.exec_module(p)

ADRES = "De Kreekrug 9"


def test_telemetrie_heeft_de_velden_van_de_server():
    bericht = p.bouw_telemetrie(ADRES, "ha-0123456789abcdef", "ha-0.1.0", {"afname": 900, "terug": 0},
                                54.4, 0.123456, {p.ROL_BOILER: 2000, p.ROL_LICHT: 60}, False)
    assert bericht["hardware_token"] == ADRES
    assert bericht["hub_id"] == "ha-0123456789abcdef"
    assert bericht["netto_p1_watt"] == 900
    assert bericht["accu_soc"] == 54
    assert bericht["beursprijs"] == 0.1235
    assert bericht["p2p_meters"] == {"boiler_watt": 2000, "ev_laadpaal_watt": 0, "airco_watt": 0,
                                     "overige_stekkers_watt": 60, "totaal_actief_p2p_watt": 2060}
    assert bericht["statussen"]["ev_ingeplugd"] is None


def test_zonder_batterij_en_prijs_stuurt_hij_null():
    bericht = p.bouw_telemetrie(ADRES, "x", "v", {"afname": 0, "terug": 300}, None, None, {}, True)
    assert bericht["accu_soc"] is None and bericht["beursprijs"] is None
    assert bericht["netto_p1_watt"] == -300


@pytest.mark.parametrize("payload,verwacht", [
    ('{"adres": "de kreekrug 9 ", "commando": "surplus_start", "vraag_toestemming": false}', ("surplus_start", False)),
    ('{"adres": "De Kreekrug 9", "commando": "surplus_stop"}', ("surplus_stop", True)),
    ('{"adres": "De Kreekrug 11", "commando": "surplus_start"}', None),
    ('{"adres": "De Kreekrug 9"}', None),
    ("kapot", None),
    ("[]", None),
])
def test_alleen_commandos_voor_dit_huis(payload, verwacht):
    assert p.lees_commando(payload, ADRES) == verwacht


def test_opdracht_moet_vers_zijn():
    vers = json.dumps({"id": "abc", "actie": "kijk", "ts": 1000})
    assert p.lees_opdracht(vers, nu=1100)["actie"] == "kijk"
    assert p.lees_opdracht(vers, nu=1121) is None
    assert p.lees_opdracht('{"id": "abc", "actie": "kijk"}', nu=1000) is None


def test_alleen_lezen_mag_zonder_bediening():
    assert not p.vraagt_bediening("kijk")
    assert not p.vraagt_bediening("gegevens")
    assert p.vraagt_bediening("modus")
    assert p.vraagt_bediening("instellingen")


def test_antwoord_zoals_de_hub():
    assert json.loads(p.bouw_antwoord("abc", None, {"x": 1})) == {"id": "abc", "ok": True, "data": {"x": 1}}
    assert json.loads(p.bouw_antwoord("abc", "kapot", None)) == {"id": "abc", "ok": False, "fout": "kapot"}


def test_prijs_van_het_huidige_kwartier():
    bericht = {"t0": 1000, "span": 900, "p": [0.1, 0.2, None]}
    assert p.prijs_nu(bericht, 1000) == 0.1
    assert p.prijs_nu(bericht, 1900) == 0.2
    assert p.prijs_nu(bericht, 2800) is None
    assert p.prijs_nu(bericht, 999) is None
    assert p.prijs_nu(None, 1000) is None


@pytest.mark.parametrize("entiteit,eenheid,soort", [
    ("switch.boiler", None, 1), ("climate.woonkamer", "°C", 1), ("light.keuken", None, 1),
    ("sensor.p1", "W", 2), ("sensor.batterij", "%", 2), ("sensor.temp", "°C", 0), ("weather.thuis", None, 0),
])
def test_entiteiten_zoals_op_de_hub(entiteit, eenheid, soort):
    assert p.entiteit_soort(entiteit, eenheid) == soort


def test_altijd_32_apparaatplekken():
    apparaten = p.normaliseer_apparaten([{"enabled": True, "name": "Boiler", "type": 6, "host": "switch.boiler", "watts": "veel"}])
    assert len(apparaten) == 32
    assert apparaten[0]["host"] == "switch.boiler" and apparaten[0]["watts"] == 2000
    assert not apparaten[1]["enabled"] and apparaten[1]["name"] == ""


def test_minuutgemiddelde_en_geen_valse_nul():
    m = p.MinuutGemiddelde()
    for w in (1000, -500, 400):
        m.voeg_toe(w, nu=10)
    assert m.neem(nu=60) == {"afname": 467, "terug": 167}
    assert m.neem(nu=100) == {"afname": 400, "terug": 0}  # vorige meting is nog vers
    assert m.neem(nu=200) is None


def test_dagtotalen_tellen_kwh_en_slaan_gaten_over():
    d = p.Dagtotalen()
    dag = date(2026, 11, 2)
    d.voeg_toe(dag, 0, pv=2000, load=500, net=-1500, bat=None)
    d.voeg_toe(dag, 3600, pv=2000, load=500, net=-1500, bat=None)   # een gat van een uur telt niet
    d.voeg_toe(dag, 3660, pv=3600, load=600, net=-3000, bat=-1000)
    vandaag = d.vandaag(dag)
    assert round(vandaag["pv"], 3) == 0.06 and round(vandaag["exp"], 3) == 0.05 and round(vandaag["chg"], 4) == 0.0167
    assert d.als_json(30)["days"][0]["date"] == 20261102


def test_apparaat_id_is_herkenbaar_en_vast():
    assert p.apparaat_id("abc") == p.apparaat_id("abc")
    assert p.apparaat_id("abc").startswith("ha-") and len(p.apparaat_id("abc")) == 19
