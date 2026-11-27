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
    o = ems_core.regel(plan, plan.geldig_tot + ems_core.VASTHOUDEN_NA_S + 10, ems_core.Meting(500, 0, 60, 1), ems_core.Grenzen())
    assert o.bron == ems_core.TERUGVAL and o.stand == ems_core.ZELFVERBRUIK and o.bat_w == 500
    o = ems_core.regel(None, 1000, ems_core.Meting(500, 0, 60, 1), ems_core.Grenzen())
    assert o.bron == ems_core.TERUGVAL and o.bat_w == 500


def test_pauze_minimum_en_hoofdzekering():
    plan = _plan()
    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(300, 0, 40, 1), ems_core.Grenzen(gepauzeerd=True))
    assert o.bron == ems_core.GEBRUIKER and o.stand == ems_core.ZELFVERBRUIK

    o = ems_core.regel(plan, plan.geldig_tot + ems_core.VASTHOUDEN_NA_S + 10, ems_core.Meting(800, 0, 5, 1), ems_core.Grenzen())
    assert o.bat_w == 0

    o = ems_core.regel(plan, plan.t0 + 60, ems_core.Meting(5500, -3000, 40, 1, 26.0), ems_core.Grenzen(zekering_a=25))
    assert o.bron == ems_core.GRENS and o.bat_w > -3000


def test_rustig_bijsturen_en_traag_volgt_het_plan():
    plan = _plan()
    o = ems_core.regel(plan, plan.geldig_tot + ems_core.VASTHOUDEN_NA_S + 10, ems_core.Meting(1000, 0, 60, 1), ems_core.Grenzen(), vorig_bat_w=0)
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


# ------------------------------------------------------------------ De kwartierwissel en Deye

T0 = 1_800_000_000 // 900 * 900


def _eigen_plan(standen, t0=T0, bat_w=None, soc=None, plan_id="p"):
    """Een plan met de gegeven standen per kwartier, vanaf t0."""
    bat_w = bat_w or [-5000 if s == ems_core.NETLADEN else 0 for s in standen]
    soc = soc or [80 if s == ems_core.NETLADEN else 50 for s in standen]
    rijen = [[s, 1000 if s in (ems_core.NETLADEN, ems_core.NETONTLADEN) else None, b, c, 0.2, 0.1] for s, b, c in zip(standen, bat_w, soc)]
    plan, fout = ems_core.lees_plan({"v": 1, "planId": plan_id, "gemaakt": t0 - 300, "geldigTot": t0 + len(rijen) * 900, "t0": t0,
                                     "stap": 900, "grenzen": {"maxLaadW": 6000, "maxOntlaadW": 6000}, "s": rijen})
    assert fout is None, fout
    return plan


def test_een_nieuw_plan_dat_pas_bij_het_volgende_kwartier_begint_laat_het_vorige_doorlopen():
    vorig = _eigen_plan([ems_core.NETLADEN] * 4, plan_id="oud")
    # 75 s voor de grens komt het nieuwe plan binnen; het begint pas bij het kwartier hierna.
    nieuw = _eigen_plan([ems_core.NETLADEN] * 4, t0=T0 + 900, plan_id="nieuw")
    nu = T0 + 900 - 75
    meting = ems_core.Meting(500, 0, 40, 1)

    zonder = ems_core.regel(nieuw, nu, meting, ems_core.Grenzen())
    assert zonder.stand == ems_core.ZELFVERBRUIK and zonder.afwijking == "buiten het plan"
    met = ems_core.regel(nieuw, nu, meting, ems_core.Grenzen(), vorig=vorig)
    assert met.bron == ems_core.PLAN and met.stand == ems_core.NETLADEN
    # Op de grens neemt het nieuwe plan het over.
    assert ems_core.plan_voor(nieuw, vorig, T0 + 900)[0] is nieuw


def test_een_verlopen_plan_houdt_het_laatste_kwartier_hooguit_30_minuten_vast():
    plan = _eigen_plan([ems_core.ZELFVERBRUIK, ems_core.VASTHOUDEN])
    einde = plan.geldig_tot
    assert ems_core.plan_voor(plan, None, einde + 600) == (plan, 1)
    assert ems_core.plan_voor(plan, None, einde + 1801) == (None, -1)


def test_laadblok_voor_een_apparaat_loopt_door_tot_het_nieuwe_plan_begint():
    vorig = _eigen_plan([ems_core.ZELFVERBRUIK] * 4, plan_id="oud")
    vorig.lasten = [{"apparaat": 1, "actie": "aan", "van": T0, "tot": T0 + 900, "stand": "laden"}]
    vorig.rust = {1: "uit"}
    nieuw = _eigen_plan([ems_core.ZELFVERBRUIK] * 4, t0=T0 + 900, plan_id="nieuw")
    nieuw.lasten = [{"apparaat": 1, "actie": "aan", "van": T0 + 900, "tot": T0 + 3600, "stand": "laden"}]
    nieuw.rust = {1: "uit"}
    nu = T0 + 900 - 75
    assert ems_core.apparaat_stand(nieuw, 1, nu).aan is False          # zo ging de lader uit
    assert ems_core.apparaat_stand(nieuw, 1, nu, vorig).aan is True     # nu loopt hij door


def _deye(plan, nu, soc, **extra):
    return ems_core.deye_wens(plan, None, nu, soc, 51, ems_core.Grenzen(), max_stroom_a=240, **extra)


def test_deye_laden_met_5000_watt_tot_80_procent():
    wens = _deye(_eigen_plan([ems_core.NETLADEN]), T0 + 60, 47)
    assert wens.laden and wens.soc == 80 and wens.stand == ems_core.NETLADEN
    assert wens.stroom_a == 98                           # 5000 W / 51 V


def test_deye_laden_nooit_voller_dan_netladen_mag():
    wens = _deye(_eigen_plan([ems_core.NETLADEN], soc=[100]), T0 + 60, 47)
    assert wens.soc == 90 and wens.beperking == "max_netladen"


def test_deye_vasthouden_op_de_lading_van_nu():
    wens = _deye(_eigen_plan([ems_core.VASTHOUDEN]), T0 + 60, 47)
    assert not wens.laden and wens.soc == 47 and wens.stand == ems_core.VASTHOUDEN
    # Nooit onder de ondergrens.
    assert _deye(_eigen_plan([ems_core.VASTHOUDEN]), T0 + 60, 12).soc == 20


def test_deye_zelfverbruik_en_ontladen_gaan_naar_de_ondergrens():
    assert (_deye(_eigen_plan([ems_core.ZELFVERBRUIK]), T0 + 60, 60).soc, _deye(_eigen_plan([ems_core.ZELFVERBRUIK]), T0 + 60, 60).laden) == (20, False)
    ontladen = _deye(_eigen_plan([ems_core.NETONTLADEN], bat_w=[3000]), T0 + 60, 60)
    assert not ontladen.laden and ontladen.soc == 20 and ontladen.beperking == "geen_vast_ontladen"


def test_deye_uit_bij_noodstop_pauze_en_zonder_plan():
    plan = _eigen_plan([ems_core.NETLADEN])
    for grenzen, beperking in ((ems_core.Grenzen(noodstop=True), "noodstop"), (ems_core.Grenzen(gepauzeerd=True), "pauze")):
        wens = ems_core.deye_wens(plan, None, T0 + 60, 47, 51, grenzen)
        assert not wens.laden and wens.soc == 20 and wens.beperking == beperking
    verlopen = ems_core.deye_wens(plan, None, plan.geldig_tot + 1801, 47, 51, ems_core.Grenzen())
    assert not verlopen.laden and verlopen.soc == 20 and verlopen.beperking == "geen_plan"


def test_deye_hoofdzekering_remt_de_laadstroom():
    wens = ems_core.deye_wens(_eigen_plan([ems_core.NETLADEN]), None, T0 + 60, 47, 51, ems_core.Grenzen(zekering_a=25),
                              max_stroom_a=240, max_fase_a=24.5)
    assert wens.laden and wens.beperking == "zekering" and wens.stroom_a < 98


def test_deye_zelfverbruik_met_negatief_vermogen_wordt_nooit_netladen():
    # 3,2 kW zon en 1,7 kW verbruik: het plan laadt 1500 W met eigen zon. Dat is geen netladen.
    wens = _deye(_eigen_plan([ems_core.ZELFVERBRUIK], bat_w=[-1500], soc=[60]), T0 + 60, 47)
    assert not wens.laden and wens.soc == 20 and wens.stroom_a is None and wens.stand == ems_core.ZELFVERBRUIK


def test_deye_zelfverbruik_dat_rond_nul_schommelt_blijft_dezelfde_opdracht():
    wensen = {(w.laden, w.soc, w.stroom_a, w.stand) for w in (
        _deye(_eigen_plan([ems_core.ZELFVERBRUIK], bat_w=[b]), T0 + 60, 47) for b in (-250, 250, -250, 250))}
    assert wensen == {(False, 20, None, ems_core.ZELFVERBRUIK)}


def test_deye_alleen_zon_met_veel_laden_houdt_de_lading_vast():
    # Laden met eigen zon in de stand alleen zon: niet ontladen tot de ondergrens.
    wens = _deye(_eigen_plan([ems_core.ALLEENZON], bat_w=[-1500]), T0 + 60, 55)
    assert not wens.laden and wens.soc == 55 and wens.stand == ems_core.VASTHOUDEN


def test_deye_netladen_met_weinig_vermogen_laadt_toch_van_het_net():
    wens = _deye(_eigen_plan([ems_core.NETLADEN], bat_w=[-100], soc=[70]), T0 + 60, 47)
    assert wens.laden and wens.soc == 70 and wens.stroom_a == 10

