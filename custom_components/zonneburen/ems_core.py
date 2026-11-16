"""De uitvoering van het energieplan, zonder Home Assistant.

Dezelfde regels als ems_core.cpp op de Zonneburen Link: de server plant elk kwartier, Home
Assistant voert het kwartier van nu uit en regelt elke paar seconden bij op de netmeting. Lokale
grenzen gaan altijd voor het plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

STANDEN = ("zelfverbruik", "netladen", "netontladen", "vasthouden", "alleenzon", "standby")
ZELFVERBRUIK, NETLADEN, NETONTLADEN, VASTHOUDEN, ALLEENZON, STANDBY = STANDEN
PLAN, TERUGVAL, GEBRUIKER, NOODSTOP, GRENS = "plan", "terugval", "gebruiker", "noodstop", "grens"

MAX_KWARTIEREN = 300
KLOK_GELDIG = 1_700_000_000
METING_TE_OUD_S = 60
MAX_APPARATEN = 32
ACTIES = ("aan", "uit")


@dataclass
class Kwartier:
    stand: str
    net_doel_w: float | None
    bat_w: float
    soc_eind: float
    prijs_in: float | None = None
    prijs_uit: float | None = None
    zon_max_w: float | None = None


@dataclass
class Plan:
    plan_id: str
    gemaakt: int
    geldig_tot: int
    t0: int
    stap: int
    kwartieren: list[Kwartier]
    soc_min: float = 0
    soc_max: float = 100
    max_laad_w: float = 0
    max_ontlaad_w: float = 0
    import_max_w: float = 0
    export_max_w: float = 0
    lasten: list[dict] = field(default_factory=list)
    verwacht: dict = field(default_factory=dict)
    # Wat een beheerd apparaat buiten zijn blokken doet: index -> "uit" of "normaal".
    rust: dict[int, str] = field(default_factory=dict)

    def index(self, nu: float) -> int:
        if nu < self.t0:
            return -1
        i = int((nu - self.t0) // self.stap)
        return i if i < len(self.kwartieren) else -1


@dataclass
class Meting:
    net_w: float | None       # + afname
    bat_w: float | None       # + ontladen
    soc: float | None
    leeftijd_s: float = 0
    max_fase_a: float | None = None


@dataclass
class Grenzen:
    max_laad_w: float = 0
    max_ontlaad_w: float = 0
    zekering_a: float = 0
    gepauzeerd: bool = False
    noodstop: bool = False


@dataclass
class Opdracht:
    stand: str
    bron: str
    bat_w: float
    net_doel_w: float
    slot: int
    afwijking: str = ""


def _getal(waarde, standaard=None):
    return float(waarde) if isinstance(waarde, (int, float)) and not isinstance(waarde, bool) else standaard


def lees_plan(bericht: dict) -> tuple[Plan | None, str | None]:
    """Controleert het plan zoals de server en de hub dat doen. Geeft (plan, None) of (None, reden)."""
    if not isinstance(bericht, dict):
        return None, "geen object"
    if bericht.get("v") != 1:
        return None, "onbekende versie"
    if bericht.get("stap") != 900:
        return None, "stap moet 900 zijn"
    gemaakt, geldig_tot, t0 = (int(_getal(bericht.get(k), 0)) for k in ("gemaakt", "geldigTot", "t0"))
    if not gemaakt or geldig_tot <= gemaakt:
        return None, "geldigTot ligt niet na gemaakt"
    if t0 % 900:
        return None, "t0 valt niet op een kwartier"
    rijen = bericht.get("s")
    if not isinstance(rijen, list) or not rijen:
        return None, "geen kwartieren"
    if len(rijen) > MAX_KWARTIEREN:
        return None, "te veel kwartieren"
    if geldig_tot > t0 + len(rijen) * 900:
        return None, "geldigTot ligt na het laatste kwartier"

    grenzen = bericht.get("grenzen") if isinstance(bericht.get("grenzen"), dict) else {}
    max_laad, max_ontlaad = _getal(grenzen.get("maxLaadW"), 0), _getal(grenzen.get("maxOntlaadW"), 0)
    soc_min, soc_max = _getal(grenzen.get("socMin"), 0), _getal(grenzen.get("socMax"), 100)
    if soc_min < 0 or soc_max > 100 or soc_min > soc_max:
        return None, "socMin en socMax kloppen niet"

    kwartieren = []
    for i, r in enumerate(rijen):
        if not isinstance(r, list) or len(r) < 4:
            return None, f"kwartier {i}: te weinig kolommen"
        if r[0] not in STANDEN:
            return None, f"kwartier {i}: onbekende stand"
        k = Kwartier(r[0], _getal(r[1]), _getal(r[2], 0), _getal(r[3], -1), _getal(r[4]) if len(r) > 4 else None,
                     _getal(r[5]) if len(r) > 5 else None, _getal(r[6]) if len(r) > 6 else None)
        if k.bat_w > max_ontlaad + 1 or -k.bat_w > max_laad + 1:
            return None, f"kwartier {i}: batW buiten de grenzen"
        if not 0 <= k.soc_eind <= 100:
            return None, f"kwartier {i}: socEind buiten 0-100"
        if k.stand in (NETLADEN, NETONTLADEN) and k.net_doel_w is None:
            return None, f"kwartier {i}: netDoelW ontbreekt"
        kwartieren.append(k)

    lasten = [last for last in bericht.get("lasten") or [] if isinstance(last, dict)]
    for i, last in enumerate(lasten):
        if "apparaat" not in last:
            continue
        index, temp = last.get("apparaat"), last.get("temp")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < MAX_APPARATEN or last.get("actie") not in ACTIES:
            return None, f"apparaatblok {i}: ongeldig apparaat of actie"
        if temp is not None and not (isinstance(temp, (int, float)) and 5 <= temp <= 80):
            return None, f"apparaatblok {i}: temperatuur buiten 5-80 graden"
        if int(_getal(last.get("tot"), 0)) <= int(_getal(last.get("van"), 0)):
            return None, f"apparaatblok {i}: eindigt niet na het begin"
    rust = {}
    for regel in bericht.get("apparaten") or []:
        if isinstance(regel, dict) and isinstance(regel.get("i"), int) and regel.get("rust") in ("uit", "normaal"):
            rust[regel["i"]] = regel["rust"]
    return Plan(str(bericht.get("planId", ""))[:40], gemaakt, geldig_tot, t0, 900, kwartieren, soc_min, soc_max,
                max_laad, max_ontlaad, _getal(grenzen.get("importMaxW"), 0), _getal(grenzen.get("exportMaxW"), 0),
                lasten, bericht.get("verwacht") if isinstance(bericht.get("verwacht"), dict) else {}, rust), None


@dataclass
class ApparaatStand:
    aan: bool | None          # None = het plan zegt niets over dit apparaat
    temp: float | None = None
    soort: str = ""           # "plan": een blok loopt, "rust": beheerd, maar nu niets te doen
    stand: str = ""


def apparaat_stand(plan: Plan | None, index: int, nu: float) -> ApparaatStand:
    """Wat het plan nu van apparaat `index` vraagt. Een verlopen plan vraagt niets meer."""
    if plan is None or nu >= plan.geldig_tot:
        return ApparaatStand(None)
    for last in plan.lasten:
        if last.get("apparaat") == index and _getal(last.get("van"), 0) <= nu < _getal(last.get("tot"), 0):
            return ApparaatStand(last.get("actie") == "aan", _getal(last.get("temp")), "plan", str(last.get("stand", "")))
    if plan.rust.get(index) == "uit":
        return ApparaatStand(False, None, "rust")
    return ApparaatStand(None)


def _grens(lokaal: float, plan: float) -> float:
    if lokaal > 0 and plan > 0:
        return min(lokaal, plan)
    return lokaal if lokaal > 0 else plan


def regel(plan: Plan | None, nu: float, m: Meting, g: Grenzen, vorig_bat_w: float | None = None,
          traag: bool = False) -> Opdracht:
    """Eén regelstap. traag = de metingen komen te langzaam binnen om op het net bij te sturen:
    dan volgt de batterij het vermogen uit het plan."""
    bat_nu = m.bat_w or 0.0
    if m.net_w is None or m.leeftijd_s > METING_TE_OUD_S:
        return Opdracht(STANDBY, GRENS, 0, (m.net_w or 0) + bat_nu, -1, "geen verse netmeting")

    stand, bron, slot, afwijking, kwartier = ZELFVERBRUIK, TERUGVAL, -1, "", None
    if g.noodstop:
        bron, afwijking = NOODSTOP, "noodstop"
    elif g.gepauzeerd:
        bron, afwijking = GEBRUIKER, "gepauzeerd"
    elif plan is None:
        afwijking = "geen plan"
    elif nu < KLOK_GELDIG:
        afwijking = "klok onbekend"
    elif nu >= plan.geldig_tot:
        afwijking = "plan verlopen"
    elif (slot := plan.index(nu)) < 0:
        afwijking = "buiten het plan"
    else:
        kwartier = plan.kwartieren[slot]
        stand, bron = kwartier.stand, PLAN

    soc = m.soc
    if kwartier and soc is not None:
        if stand == NETLADEN and soc >= kwartier.soc_eind - 0.5:
            stand, afwijking = VASTHOUDEN, "doel-lading bereikt"
        elif stand == NETONTLADEN and soc <= kwartier.soc_eind + 0.5:
            stand, afwijking = VASTHOUDEN, "doel-lading bereikt"

    residu = m.net_w + bat_nu
    if stand == NETLADEN:
        net_doel = kwartier.net_doel_w
        doel = kwartier.bat_w if traag else bat_nu + (m.net_w - net_doel)
        doel = max(min(doel, 0), kwartier.bat_w if kwartier.bat_w < 0 else -math.inf)
    elif stand == NETONTLADEN:
        net_doel = kwartier.net_doel_w
        doel = kwartier.bat_w if traag else bat_nu + (m.net_w - net_doel)
        doel = min(max(doel, 0), kwartier.bat_w if kwartier.bat_w > 0 else math.inf)
    elif stand in (VASTHOUDEN, ALLEENZON):
        doel, net_doel = min(residu, 0), max(residu, 0)
    elif stand == STANDBY:
        doel, net_doel = 0, residu
    else:
        doel, net_doel = residu, 0

    max_laad = _grens(g.max_laad_w, plan.max_laad_w if plan else 0) or 3000
    max_ontlaad = _grens(g.max_ontlaad_w, plan.max_ontlaad_w if plan else 0) or 3000
    doel = min(max(doel, -max_laad), max_ontlaad)
    if soc is not None and plan:
        if soc <= plan.soc_min and doel > 0:
            doel, afwijking = 0, afwijking or "batterij op minimum"
        if soc >= plan.soc_max and doel < 0:
            doel, afwijking = 0, afwijking or "batterij vol"

    if stand != STANDBY and vorig_bat_w is not None:
        doel = vorig_bat_w + 0.7 * (doel - vorig_bat_w)

    net_na = m.net_w + bat_nu - doel
    limiet = plan.import_max_w if plan and plan.import_max_w > 0 else 0
    if g.zekering_a > 0 and m.max_fase_a is not None and m.max_fase_a > 0.95 * g.zekering_a:
        doel += (m.max_fase_a - 0.9 * g.zekering_a) * 230
        bron, afwijking = GRENS, f"hoofdzekering {m.max_fase_a:.0f} A"
    elif limiet and net_na > limiet:
        doel += net_na - limiet
        bron, afwijking = GRENS, f"afname boven {limiet:.0f} W"
    doel = min(max(doel, -max_laad), max_ontlaad)
    if soc is not None and plan and soc <= plan.soc_min and doel > 0:
        doel = 0

    doel = round(doel)
    if bron == GRENS:
        net_doel = m.net_w + bat_nu - doel
    return Opdracht(stand, bron, doel, net_doel, slot, afwijking)


ZINNEN = {
    ZELFVERBRUIK: "Eigen stroom gebruiken", NETLADEN: "Goedkoop bijladen van het net",
    NETONTLADEN: "Terugleveren uit de batterij", VASTHOUDEN: "Lading bewaren voor later",
    ALLEENZON: "Alleen zon opslaan", STANDBY: "Batterij rust",
}


def blokken(plan: Plan, nu: float, maximaal: int = 6) -> list[dict]:
    """Het plan in blokken van dezelfde stand, vanaf nu: voor de sensor en de kaart."""
    uit: list[dict] = []
    start = max(plan.index(nu), 0)
    for i in range(start, len(plan.kwartieren)):
        k = plan.kwartieren[i]
        van = plan.t0 + i * plan.stap
        if uit and uit[-1]["stand"] == k.stand and uit[-1]["tot"] == van:
            uit[-1]["tot"] = van + plan.stap
        else:
            if len(uit) >= maximaal:
                break
            uit.append({"stand": k.stand, "van": van, "tot": van + plan.stap, "zin": ZINNEN[k.stand]})
    return uit


BLOK_ZINNEN = {"laden": "Laden", "aan": "Aan", "aanraden": "Voorverwarmen", "blokkeren": "Pauze"}
REDEN_ZINNEN = {"voorverwarmen": "Voorverwarmen", "voorkoelen": "Voorkoelen", "pauze": "Pauze, stroom is nu duur"}


def blok_zin(blok: dict) -> str:
    """Wat een apparaat in een blok doet, in woorden: "Voorverwarmen tot 55 °C"."""
    zin = REDEN_ZINNEN.get(blok.get("reden", "")) or BLOK_ZINNEN.get(blok.get("stand", "")) or ("Aan" if blok.get("actie") == "aan" else "Uit")
    if blok.get("temp") is not None:
        zin += f" ({blok['temp']:g} °C)"
    elif blok.get("actie") == "uit" and zin == "Pauze, stroom is nu duur":
        zin = "Uit, stroom is nu duur"
    return zin


def apparaat_blokken(plan: Plan | None, nu: float, namen: dict[int, str] | None = None, index: int | None = None,
                     maximaal: int = 24) -> list[dict]:
    """De blokken voor apparaten die nog lopen of nog komen, op volgorde: voor de sensoren en de kaart.
    Ook blokken zonder apparaat (alleen advies) staan erin, met i = None."""
    if plan is None or nu >= plan.geldig_tot:
        return []
    namen = namen or {}
    uit: list[dict] = []
    for last in plan.lasten:
        i = last.get("apparaat")
        if isinstance(i, bool) or not isinstance(i, int):
            i = None
        if index is not None and i != index:
            continue
        van, tot = int(_getal(last.get("van"), 0)), int(min(_getal(last.get("tot"), 0), plan.geldig_tot))
        if tot <= nu or tot <= van:
            continue
        blok = {"i": i, "naam": (namen.get(i) if i is not None else None) or str(last.get("naam") or last.get("id") or "Apparaat")[:40],
                "van": van, "tot": tot, "stand": str(last.get("stand", ""))[:20], "actie": last.get("actie") if i is not None else None}
        if (temp := _getal(last.get("temp"))) is not None:
            blok["temp"] = temp
        if last.get("reden"):
            blok["reden"] = str(last["reden"])[:20]
        blok["zin"] = blok_zin(blok)
        uit.append(blok)
    uit.sort(key=lambda b: (b["van"], b["i"] if b["i"] is not None else 99))
    return uit[:maximaal]
