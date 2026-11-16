"""De blokken voor apparaten zoals de sensoren en de kaart ze tonen."""

import copy
import json
import pathlib

from custom_components.zonneburen import ems_core

VOORBEELD = json.loads((pathlib.Path(__file__).parent / "plan_voorbeeld.json").read_text(encoding="utf-8"))


def _plan(lasten):
    bericht = copy.deepcopy(VOORBEELD)
    t0 = bericht["t0"]
    bericht["lasten"] = [{**l, "van": t0 + l["van"], "tot": t0 + l["tot"]} for l in lasten]
    plan, fout = ems_core.lees_plan(bericht)
    assert fout is None
    return plan


def test_blokken_op_volgorde_met_naam_uit_de_apparaten():
    plan = _plan([
        {"id": "boiler", "stand": "aanraden", "van": 7200, "tot": 9000, "apparaat": 1, "actie": "aan", "temp": 60, "reden": "voorverwarmen"},
        {"id": "ev", "stand": "laden", "van": 3600, "tot": 7200, "apparaat": 0, "actie": "aan"},
    ])
    blokken = ems_core.apparaat_blokken(plan, plan.t0, {0: "Laadpaal", 1: "Boiler"})
    assert [b["naam"] for b in blokken] == ["Laadpaal", "Boiler"]
    assert blokken[0]["zin"] == "Laden"
    assert blokken[1]["zin"] == "Voorverwarmen (60 °C)"


def test_alleen_komende_blokken_en_per_apparaat():
    plan = _plan([
        {"id": "ev", "stand": "laden", "van": 0, "tot": 900, "apparaat": 0, "actie": "aan"},
        {"id": "ev", "stand": "laden", "van": 3600, "tot": 4500, "apparaat": 0, "actie": "aan"},
        {"id": "wp", "stand": "blokkeren", "van": 3600, "tot": 4500, "apparaat": 2, "actie": "uit", "reden": "pauze"},
    ])
    nu = plan.t0 + 1000
    assert [b["van"] - plan.t0 for b in ems_core.apparaat_blokken(plan, nu)] == [3600, 3600]
    alleen_wp = ems_core.apparaat_blokken(plan, nu, index=2)
    assert len(alleen_wp) == 1 and alleen_wp[0]["zin"] == "Uit, stroom is nu duur"


def test_advies_zonder_apparaat_houdt_de_naam_uit_het_plan():
    plan = _plan([{"id": "vaatwasser", "naam": "Vaatwasser", "stand": "aan", "van": 900, "tot": 4500}])
    blok = ems_core.apparaat_blokken(plan, plan.t0)[0]
    assert blok["i"] is None and blok["naam"] == "Vaatwasser" and blok["actie"] is None


def test_verlopen_plan_heeft_geen_blokken():
    plan = _plan([{"id": "ev", "stand": "laden", "van": 0, "tot": 900, "apparaat": 0, "actie": "aan"}])
    assert ems_core.apparaat_blokken(plan, plan.geldig_tot + 1) == []
    assert ems_core.apparaat_blokken(None, 0) == []
