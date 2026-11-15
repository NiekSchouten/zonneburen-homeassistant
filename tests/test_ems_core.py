"""De kern van het energieplan, zonder Home Assistant: dezelfde gevallen als de hub (test_core.cpp)."""

import copy
import json
import math
import pathlib

from custom_components.zonneburen import ems_core

VOORBEELD = json.loads((pathlib.Path(__file__).parent / "plan_voorbeeld.json").read_text(encoding="utf-8"))


def _plan():
    plan, fout = ems_core.lees_plan(VOORBEELD)
    assert fout is None
    return plan


def test_leest_het_voorbeeldplan_van_de_server():
    plan = _plan()
    assert len(plan.kwartieren) >= 144
    assert plan.kwartieren[0].stand == ems_core.NETLADEN
    assert plan.max_laad_w == 3000
    assert plan.lasten and plan.verwacht["besparingBatterij"] > 0


def test_weigert_een_kapot_plan():
    for aanpassen, reden in (
        (lambda b: b.update(stap=600), "stap moet 900 zijn"),
        (lambda b: b["s"][3].__setitem__(0, "turbo"), "kwartier 3: onbekende stand"),
        (lambda b: b["s"][0].__setitem__(2, -9000), "kwartier 0: batW buiten de grenzen"),
        (lambda b: b.update(geldigTot=b["gemaakt"]), "geldigTot ligt niet na gemaakt"),
    ):
        bericht = copy.deepcopy(VOORBEELD)
        aanpassen(bericht)
        plan, fout = ems_core.lees_plan(bericht)
        assert plan is None and fout == reden


def test_netladen_tot_het_netdoel_binnen_de_grens():
    plan = _plan()
    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(300, 0, 40, 1), ems_core.Grenzen())
    assert o.bron == ems_core.PLAN and o.stand == ems_core.NETLADEN and o.slot == 0
    assert o.bat_w == -3000
    assert o.net_doel_w == plan.kwartieren[0].net_doel_w


def test_doel_lading_bereikt_dan_bewaren():
    plan = _plan()
    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(500, 0, 48, 1), ems_core.Grenzen())
    assert o.stand == ems_core.VASTHOUDEN and o.bat_w == 0


def test_zonder_verse_meting_rust():
    plan = _plan()
    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(500, 0, 40, 90), ems_core.Grenzen())
    assert o.stand == ems_core.STANDBY and o.bron == ems_core.GRENS and o.bat_w == 0


def test_verlopen_plan_en_geen_plan_geven_zelfverbruik():
    plan = _plan()
    o = ems_core.regel(plan, plan.geldig_tot + 10, ems_core.Meting(500, 0, 60, 1), ems_core.Grenzen())
    assert o.bron == ems_core.TERUGVAL and o.stand == ems_core.ZELFVERBRUIK and o.bat_w == 500
    o = ems_core.regel(None, 1000, ems_core.Meting(500, 0, 60, 1), ems_core.Grenzen())
    assert o.bron == ems_core.TERUGVAL and o.bat_w == 500


def test_pauze_minimum_en_hoofdzekering():
    plan = _plan()
    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(300, 0, 40, 1), ems_core.Grenzen(gepauzeerd=True))
    assert o.bron == ems_core.GEBRUIKER and o.stand == ems_core.ZELFVERBRUIK

    o = ems_core.regel(plan, plan.geldig_tot + 10, ems_core.Meting(800, 0, 5, 1), ems_core.Grenzen())
    assert o.bat_w == 0

    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(5500, -3000, 40, 1, 26.0), ems_core.Grenzen(zekering_a=25))
    assert o.bron == ems_core.GRENS and o.bat_w > -3000


def test_rustig_bijsturen_en_traag_volgt_het_plan():
    plan = _plan()
    o = ems_core.regel(plan, plan.geldig_tot + 10, ems_core.Meting(1000, 0, 60, 1), ems_core.Grenzen(), vorig_bat_w=0)
    assert math.isclose(o.bat_w, 700, abs_tol=1)
    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(-2000, 0, 40, 1), ems_core.Grenzen(), traag=True)
    assert o.bat_w == plan.kwartieren[0].bat_w


def test_blokken_voor_de_kaart():
    plan = _plan()
    blokken = ems_core.blokken(plan, plan.t0)
    assert blokken[0]["stand"] == ems_core.NETLADEN and blokken[0]["van"] == plan.t0
    assert all(b["tot"] > b["van"] for b in blokken)


def _met_apparaten(lasten, apparaten=()):
    bericht = copy.deepcopy(VOORBEELD)
    bericht["lasten"] = lasten
    bericht["apparaten"] = list(apparaten)
    return bericht


def test_apparaat_volgt_het_blok_waarin_het_nu_zit():
    t0 = VOORBEELD["t0"]
    plan, fout = ems_core.lees_plan(_met_apparaten(
        [{"id": "wp", "stand": "aanraden", "maxW": 900, "van": t0, "tot": t0 + 1800, "apparaat": 2, "actie": "aan", "temp": 21},
         {"id": "wp", "stand": "blokkeren", "maxW": 900, "van": t0 + 3600, "tot": t0 + 5400, "apparaat": 3, "actie": "aan"}],
        [{"i": 2, "rust": "normaal"}, {"i": 3, "rust": "uit"}]))
    assert fout is None

    stand = ems_core.apparaat_stand(plan, 2, t0 + 60)
    assert (stand.aan, stand.temp, stand.soort) == (True, 21, "plan")
    assert ems_core.apparaat_stand(plan, 2, t0 + 2000).soort == ""           # normaal: het plan zegt niets
    assert ems_core.apparaat_stand(plan, 3, t0 + 2000).soort == "rust"       # blokkeercontact: uit
    assert ems_core.apparaat_stand(plan, 3, t0 + 4000).aan is True           # blokkeren
    assert ems_core.apparaat_stand(plan, 7, t0).aan is None                  # geen beheerd apparaat


def test_verlopen_plan_stuurt_geen_apparaten_meer():
    t0 = VOORBEELD["t0"]
    plan, _ = ems_core.lees_plan(_met_apparaten([{"id": "ev", "stand": "laden", "maxW": 3700, "van": t0, "tot": t0 + 900000,
                                                  "apparaat": 1, "actie": "aan"}], [{"i": 1, "rust": "uit"}]))
    assert ems_core.apparaat_stand(plan, 1, plan.geldig_tot + 1).aan is None


def test_weigert_een_ongeldig_apparaatblok():
    t0 = VOORBEELD["t0"]
    for last, reden in (
        ({"van": t0, "tot": t0 + 900, "apparaat": 40, "actie": "aan"}, "apparaatblok 0: ongeldig apparaat of actie"),
        ({"van": t0, "tot": t0 + 900, "apparaat": 1, "actie": "turbo"}, "apparaatblok 0: ongeldig apparaat of actie"),
        ({"van": t0, "tot": t0 + 900, "apparaat": 1, "actie": "aan", "temp": 99}, "apparaatblok 0: temperatuur buiten 5-80 graden"),
        ({"van": t0, "tot": t0, "apparaat": 1, "actie": "aan"}, "apparaatblok 0: eindigt niet na het begin"),
    ):
        plan, fout = ems_core.lees_plan(_met_apparaten([last]))
        assert plan is None and fout == reden
