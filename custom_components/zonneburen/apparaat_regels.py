"""Veilige regels voor het sturen van apparaten, zonder Home Assistant: per vermogen, niet per merk.

- Verwarming, koeling en boilers gaan nooit uit (tenzij de bewoner dat per apparaat toestaat): Zonneburen
  verschuift alleen de gewenste temperatuur, binnen comfortgrenzen.
- Een wasmachine, vaatwasser of droger gaat nooit uit terwijl hij draait.
- Een cloud-integratie krijgt hooguit één schrijfactie per half uur per entiteit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Wat een apparaat is, afgeleid uit het domein en de soort uit het energieplan.
THERMISCH, PROGRAMMA, SCHAKELAAR = "thermisch", "programma", "schakelaar"
# Wat Zonneburen met een thermisch apparaat wil.
VERHOGEN, NORMAAL, BESPAREN, UITZETTEN = "verhogen", "normaal", "besparen", "uit"

PLAN_WARMTE = ("wp", "airco", "verwarming")
PLAN_BOILER = "boiler"
PLAN_PROGRAMMA = "witgoed"

STIL_W = 5              # onder dit vermogen staat een apparaat stil
LOOPT_W = 10            # boven dit vermogen draait het
STIL_S = 120            # zo lang stil voordat de stekker uit mag
KLAAR_S = 300           # zo lang stil na een programma: klaar
CLOUD_S = 30 * 60       # schrijflimiet voor cloud-integraties
LOKAAL_S = 60           # en voor lokale
RATELIMIT_MIN = 30      # minder aanroepen over vandaag: stoppen met sturen
HANDMATIG_PAUZE_S = 4 * 3600
NAIJLEN_S = 15 * 60     # zo lang mag een cloud-integratie achterlopen voordat een verschil handmatig is
BESPAREN_MAX_S = 2 * 3600

STANDAARD = {
    "omhoog": 1.0,       # voorverwarmen: zoveel boven de basis
    "omlaag": -0.5,      # besparen: zoveel onder de basis
    "min_temp": 19.0,    # comfortgrenzen voor de kamer
    "max_temp": 22.0,
    "opwarmen": 50.0,    # boiler: opwarmen tot
    "boiler_max": 60.0,
}


def klasse(domein: str, plan_soort: str | None) -> str:
    """Thermisch (climate, water_heater), programma (witgoed op een stekker) of een gewone schakelaar."""
    if domein in ("climate", "water_heater"):
        return THERMISCH
    if plan_soort == PLAN_PROGRAMMA:
        return PROGRAMMA
    return SCHAKELAAR


def thermische_wens(reden: str, aan: bool, plan_stand: str = "", mag_uit: bool = False) -> str:
    """Van wat de hub wil (aan/uit met een reden) naar wat een thermisch apparaat doet. Nooit uit, tenzij
    de bewoner dat voor dit apparaat toestaat."""
    if reden == "Handmatig uit":
        return UITZETTEN if mag_uit else NORMAAL
    if reden.startswith("Energieplan"):
        if plan_stand == "blokkeren" or reden == "Energieplan: pauze":
            # Een pauze verlaagt de temperatuur; alleen uit als de bewoner dat voor dit apparaat toestaat.
            return UITZETTEN if mag_uit and not aan else BESPAREN
        # Buiten een blok ("wacht op het beste moment") gewoon de basis: nooit uit.
        return VERHOGEN if aan else NORMAAL
    if reden in ("Handmatig aan", "Wijkstroom"):
        return VERHOGEN
    return NORMAAL


def _rond(waarde: float, stap: float) -> float:
    stap = stap if stap and stap > 0 else 0.5
    return round(round(waarde / stap) * stap, 2)


@dataclass
class Grenzen:
    """Wat de entiteit zelf toestaat."""
    tmin: float
    tmax: float
    stap: float = 0.5
    koelt: bool = False


def thermisch_doel(domein: str, wens: str, basis: float, g: Grenzen, plan_temp: float | None = None,
                   instellingen: dict | None = None) -> float:
    """De doeltemperatuur die bij de wens past. Altijd binnen de grenzen van de entiteit en binnen de
    comfortgrenzen; een boiler nooit kouder dan zijn basis."""
    s = {**STANDAARD, **(instellingen or {})}
    if domein == "water_heater":
        laag, hoog = basis, max(basis, min(g.tmax, s["boiler_max"]))
        doel = basis
        if wens == VERHOGEN:
            doel = plan_temp if plan_temp is not None else s["opwarmen"]
        return _rond(min(max(doel, laag), hoog), g.stap)

    omhoog, omlaag = abs(s["omhoog"]), -abs(s["omlaag"])
    if g.koelt:   # bij koelen is "voorkoelen" kouder en "besparen" warmer
        omhoog, omlaag = -omhoog, -omlaag
    if wens == VERHOGEN:
        doel = plan_temp if plan_temp is not None else basis + omhoog
    elif wens == BESPAREN:
        doel = basis + omlaag
    else:
        doel = basis
    # Nooit verder dan de verschuiving van de basis, ook niet als het plan iets anders vraagt.
    doel = min(max(doel, basis + min(omhoog, omlaag)), basis + max(omhoog, omlaag))
    comfort_laag, comfort_hoog = min(s["min_temp"], basis), max(s["max_temp"], basis)
    doel = min(max(doel, comfort_laag), comfort_hoog)
    return _rond(min(max(doel, g.tmin), g.tmax), g.stap)


def is_handmatig(doel_nu: float | None, verwacht: float | None, geschreven_op: float, nu: float, stap: float,
                 naijlen_s: float = NAIJLEN_S) -> bool:
    """Heeft iets anders dan Zonneburen de temperatuur veranderd? Pas na de tijd die de integratie mag
    achterlopen: een cloud meldt een nieuwe waarde soms pas na een paar minuten."""
    if doel_nu is None or verwacht is None:
        return False
    return abs(doel_nu - verwacht) > max(stap, 0.1) / 2 and nu - geschreven_op >= naijlen_s


@dataclass
class Stilstand:
    """Houdt per apparaat bij of het draait, op zijn vermogenssensor."""
    actief_op: float | None = None    # laatst boven STIL_W
    loopt_op: float | None = None     # laatst boven LOOPT_W
    gemeten: bool = False

    def meet(self, watt: float | None, nu: float) -> None:
        if watt is None:
            return
        if not self.gemeten:
            self.gemeten = True
            if watt < STIL_W:
                self.actief_op = nu - STIL_S   # net gekoppeld en stil: niet eerst twee minuten wachten
        if watt >= STIL_W:
            self.actief_op = nu
        if watt > LOOPT_W:
            self.loopt_op = nu

    def stil_sinds(self, nu: float) -> float:
        """Hoe lang het apparaat al stil staat; 0 als het draait of niet gemeten is."""
        if not self.gemeten:
            return 0
        return nu - self.actief_op if self.actief_op is not None else STIL_S

    def mag_uit(self, nu: float) -> bool:
        """Alleen uit als het minstens twee minuten stil staat. Zonder meting: nooit automatisch."""
        return self.gemeten and self.stil_sinds(nu) >= STIL_S

    def draait(self, nu: float) -> bool:
        return self.gemeten and self.loopt_op is not None and nu - self.loopt_op < STIL_S


@dataclass
class Schrijflimiet:
    """Hooguit één schrijfactie per interval per entiteit, en alleen bij een echte verandering."""
    geschreven: dict[str, tuple[float, object]] = field(default_factory=dict)

    def mag(self, entity_id: str, waarde: object, nu: float, interval: float) -> bool:
        vorige = self.geschreven.get(entity_id)
        if vorige is None:
            return True
        op, oud = vorige
        if oud == waarde:
            return False
        return nu - op >= interval

    def zelfde(self, entity_id: str, waarde: object) -> bool:
        vorige = self.geschreven.get(entity_id)
        return vorige is not None and vorige[1] == waarde

    def noteer(self, entity_id: str, waarde: object, nu: float) -> None:
        self.geschreven[entity_id] = (nu, waarde)

    def op(self, entity_id: str) -> float:
        vorige = self.geschreven.get(entity_id)
        return vorige[0] if vorige else 0.0


def programma_wens(plan_aan: bool | None, klaargezet: bool, loopt_door: bool, nu_aan: bool | None,
                   stil: Stilstand, nu: float, uit_als_ongebruikt: bool = False, start_uiterlijk: float | None = None) -> bool | None:
    """Wat de stekker van een wasmachine, vaatwasser of droger moet doen. None: niets doen.

    - Klaargezet ("start slim"): uit tot het blok van het plan of het laatste startmoment, dan aan.
    - Eenmaal gestart blijft hij aan tot het programma klaar is, ook na het einde van het blok.
    - Daarna blijft hij aan, zodat de machine gewoon te gebruiken is; tenzij de bewoner anders kiest.
    - Nooit uit terwijl hij draait."""
    if loopt_door:
        return True
    if klaargezet:
        return bool(plan_aan or (start_uiterlijk is not None and nu >= start_uiterlijk))
    if plan_aan:
        return True
    if uit_als_ongebruikt and nu_aan and stil.mag_uit(nu):
        return False
    return None
