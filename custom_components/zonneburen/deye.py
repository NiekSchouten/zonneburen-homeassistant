"""Een Deye of Sunsynk sturen via zijn tijdprogramma's (Time of Use).

Zo'n omvormer kent geen vermogens-setpoint in W. Wel, via bijvoorbeeld de Solarman-integratie: een werkmodus
(Zero Export To CT voor zelfverbruik op de meter, Export First om naar het net te leveren), zes tijdprogramma's
met elk een keuze om van het net te laden (Grid of Disabled), een lading (SOC) en een vermogen, plus stromen
voor laden en ontladen. Zonneburen zet de zes programma's op vaste tijden (00:00, 04:00 ... 20:00) en geeft ze
allemaal dezelfde opdracht: zo doet de omvormer de hele dag wat het kwartier van nu vraagt.

Bij het starten bewaren we de instellingen van de bewoner; bij uitzetten, herladen of verwijderen gaan die
terug, net als bij een pauze, een noodstop of zonder geldig plan.

Zuinig met schrijven (EEPROM, Modbus): alleen wat echt afwijkt, per entiteit hooguit eens per vijf minuten
(behalve bij een andere stand), in een vaste volgorde, elke opdracht met een time-out. Drie mislukte rondes
op rij: een melding bij Reparaties en terug naar de eigen instellingen.
"""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import ems_core
from .const import (
    CONF_EMS_ZEKERING_A,
    CONF_TOU,
    CONF_TOU_FASEN,
    CONF_TOU_LADEN,
    CONF_TOU_MAX_EXPORT,
    CONF_TOU_MAX_LAAD_A,
    CONF_TOU_MAX_LAAD_W,
    CONF_TOU_MAX_ONTLAAD_A,
    CONF_TOU_MAX_ONTLAAD_W,
    CONF_TOU_MAX_PCT,
    CONF_TOU_MIN_PCT,
    CONF_TOU_MIN_SOC,
    CONF_TOU_NETLADEN,
    CONF_TOU_PATROON,
    CONF_TOU_SOC,
    CONF_TOU_SPANNING,
    CONF_TOU_STROOM,
    CONF_TOU_TERUGLEVEREN,
    CONF_TOU_TIJD,
    CONF_TOU_VERMOGEN,
    CONF_TOU_WERKMODUS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

RONDE_S = 10                 # hoe vaak we kijken (en de stap van de hoofdzekering)
PER_ENTITEIT_S = 5 * 60      # hooguit eens per vijf minuten per entiteit, behalve bij een andere stand
TIMEOUT_S = 10
SOC_MARGE = 2
STROOM_MARGE_A = 5
VERMOGEN_MARGE_W = 250
MAX_FOUTEN = 3
ZEKERING_STAP_A = 10
TIJDEN = ("00:00:00", "04:00:00", "08:00:00", "12:00:00", "16:00:00", "20:00:00")
ISSUE = "deye_schrijven"


def _getal(waarde) -> float | None:
    try:
        return float(waarde)
    except (TypeError, ValueError):
        return None


def optie(opties: list[str], soort: str) -> str | None:
    """Een optie uit de lijst van de omvormer: grid, uit, aan, ct, export of battery_first."""
    plat = {o: o.lower() for o in opties}
    zoek = lambda *test: next((o for o, p in plat.items() if any(t(p) for t in test)), None)
    if soort == "grid":
        return zoek(lambda p: p == "grid") or zoek(lambda p: "grid" in p and "gen" not in p) or zoek(lambda p: "grid" in p)
    if soort == "uit":
        return zoek(lambda p: p in ("disabled", "disable", "none", "off", "uit")) or zoek(lambda p: "disab" in p or p.startswith("no"))
    if soort == "aan":
        return zoek(lambda p: p in ("enabled", "enable", "on", "aan")) or zoek(lambda p: "enab" in p and "disab" not in p)
    if soort == "ct":
        return zoek(lambda p: "zero export" in p and "ct" in p) or zoek(lambda p: "ct" in p.split())
    if soort == "export":
        return zoek(lambda p: "export first" in p) or zoek(lambda p: p.startswith("export"))
    if soort == "battery_first":
        return zoek(lambda p: "battery first" in p) or zoek(lambda p: p.startswith("battery"))
    return None


class DeyeSturing:
    def __init__(self, ems) -> None:
        self.ems = ems
        self.laatste_opdracht: dict | None = None
        self.wens: ems_core.DeyeWens | None = None
        self.opgeslagen: dict[str, str] = {}
        self._store: Store | None = None
        self._geladen = False
        self._volgende = 0.0
        self._fouten = 0
        self._geschreven: dict[str, float] = {}
        self._toegepast: str | None = None
        self.zekering_rem_a = 0.0

    @property
    def hass(self):
        return self.ems.hub.hass

    @property
    def opties(self) -> dict:
        return self.ems.opties

    def _lijst(self, sleutel: str) -> list[str]:
        waarde = self.opties.get(sleutel) or []
        return [w for w in (waarde if isinstance(waarde, list) else [waarde]) if w]

    def klaar(self) -> bool:
        """Genoeg ingesteld om te sturen: de programma's met hun laden-keuze en hun lading."""
        return bool(self._lijst(CONF_TOU_LADEN) and self._lijst(CONF_TOU_SOC))

    # ------------------------------------------------------------------ Instellingen en metingen

    def _staat(self, entity_id: str | None):
        staat = self.hass.states.get(entity_id) if entity_id else None
        return None if staat is None or staat.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) else staat

    def spanning(self) -> float | None:
        staat = self._staat(self.opties.get(CONF_TOU_SPANNING))
        return _getal(staat.state) if staat else None

    def min_pct(self) -> float:
        """De ondergrens van de omvormer zelf als die bekend is, anders de instelling (standaard 20 %)."""
        staat = self._staat(self.opties.get(CONF_TOU_MIN_SOC))
        eigen = _getal(staat.state) if staat else None
        return eigen if eigen is not None and 0 <= eigen <= 100 else float(self.opties.get(CONF_TOU_MIN_PCT) or 20)

    def max_pct(self) -> float:
        return float(self.opties.get(CONF_TOU_MAX_PCT) or 90)

    def max_laad_w(self) -> float:
        return float(self.opties.get(CONF_TOU_MAX_LAAD_W) or 10000)

    def max_ontlaad_w(self) -> float:
        return float(self.opties.get(CONF_TOU_MAX_ONTLAAD_W) or 10000)

    def terugleveren(self) -> bool:
        return bool(self.opties.get(CONF_TOU_TERUGLEVEREN))

    def max_stroom(self) -> float | None:
        staat = self.hass.states.get(self.opties.get(CONF_TOU_STROOM) or "")
        return _getal(staat.attributes.get("max")) if staat else None

    def fase_max(self) -> float | None:
        waarden = [_getal(s.state) for i in self._lijst(CONF_TOU_FASEN) if (s := self._staat(i))]
        waarden = [w for w in waarden if w is not None]
        return max(waarden) if waarden else None

    def _beheerd(self) -> list[str]:
        """Alles wat Zonneburen bij deze omvormer kan zetten, in de volgorde van schrijven."""
        enkel = [self.opties.get(k) for k in (CONF_TOU_WERKMODUS, CONF_TOU_PATROON, CONF_TOU_NETLADEN, CONF_TOU_STROOM,
                                             CONF_TOU_MAX_LAAD_A, CONF_TOU_MAX_ONTLAAD_A, CONF_TOU_MAX_EXPORT)]
        lijsten = [*self._lijst(CONF_TOU_LADEN), *self._lijst(CONF_TOU_SOC), *self._lijst(CONF_TOU_VERMOGEN)]
        return [i for i in (*enkel, *lijsten, self.opties.get(CONF_TOU), *self._lijst(CONF_TOU_TIJD)) if i]

    # ------------------------------------------------------------------ De instellingen van de bewoner

    async def laad(self) -> None:
        if self._geladen:
            return
        self._geladen = True
        self._store = Store(self.hass, 1, f"{DOMAIN}.deye_{self.ems.hub.entry.entry_id}")
        bewaard = await self._store.async_load()
        self.opgeslagen = dict(bewaard.get("waarden") or {}) if isinstance(bewaard, dict) else {}

    async def bewaar_eigen_stand(self) -> None:
        """De instellingen van nu, als de bewoner ze had. Al bewaard: niets doen (dan zijn het onze waarden)."""
        await self.laad()
        if self.opgeslagen:
            return
        waarden = {i: s.state for i in self._beheerd() if (s := self._staat(i))}
        tijden = [waarden.get(i, "")[:5] for i in self._lijst(CONF_TOU_TIJD)]
        laden = {waarden.get(i) for i in self._lijst(CONF_TOU_LADEN)}
        # Stond Zonneburen al te sturen (0.8), dan zijn dit onze waarden: bewaar een veilige eigen stand.
        if tijden and tijden == [t[:5] for t in TIJDEN[:len(tijden)]] and len(laden) == 1:
            for i in self._lijst(CONF_TOU_LADEN):
                if (s := self._staat(i)) and (uit := optie([str(o) for o in s.attributes.get("options") or []], "uit")):
                    waarden[i] = uit
            for i in self._lijst(CONF_TOU_SOC):
                waarden[i] = str(round(self.min_pct()))
            if (s := self._staat(self.opties.get(CONF_TOU_WERKMODUS))) and (ct := optie([str(o) for o in s.attributes.get("options") or []], "ct")):
                waarden[s.entity_id] = ct
            _LOGGER.info("Zonneburen: de tijdprogramma's stonden al op Zonneburen; als eigen stand geldt zelfverbruik tot %s %%", round(self.min_pct()))
        self.opgeslagen = waarden
        if self._store:
            await self._store.async_save({"waarden": waarden, "op": dt_util.utcnow().isoformat()})

    async def herstel(self, wissen: bool = False) -> bool:
        """De eigen instellingen van de bewoner terug. Wissen: daarna vergeten (sturing uit, herladen, verwijderen)."""
        await self.laad()
        if not self.opgeslagen:
            return True
        gelukt = await self.zet(ems_core.DeyeWens(False, self.min_pct(), None, "eigen stand", ems_core.TERUGVAL, "uit", herstel=True),
                                forceer=True)
        if gelukt and wissen:
            self.opgeslagen = {}
            self._toegepast = None
            if self._store:
                await self._store.async_remove()
        return gelukt

    # ------------------------------------------------------------------ Wat we willen

    def _gewenst(self, wens: ems_core.DeyeWens) -> list[tuple[str, object]]:
        """Per entiteit de gewenste waarde, in de volgorde van schrijven: werkmodus, stromen, programma's."""
        eigen = self.opgeslagen
        uit: list[tuple[str, object]] = []

        def keuze(entity_id: str | None, soort: str) -> None:
            if not entity_id or not (s := self._staat(entity_id)):
                return
            waarde = eigen.get(entity_id) if wens.herstel else optie([str(o) for o in s.attributes.get("options") or []], soort)
            if waarde:
                uit.append((entity_id, waarde))

        def getal(entity_id: str | None, waarde: float | None) -> None:
            if not entity_id:
                return
            if wens.herstel or waarde is None:
                waarde = _getal(eigen.get(entity_id))
            if waarde is not None:
                uit.append((entity_id, waarde))

        keuze(self.opties.get(CONF_TOU_WERKMODUS), "export" if wens.werkmodus == ems_core.EXPORT else "ct")
        keuze(self.opties.get(CONF_TOU_PATROON), "battery_first")
        if (schakelaar := self.opties.get(CONF_TOU_NETLADEN)) and self._staat(schakelaar):
            waarde = eigen.get(schakelaar) if wens.herstel else ("on" if wens.netladen_aan else "off")
            if waarde in ("on", "off"):
                uit.append((schakelaar, waarde))
        stroom = wens.stroom_a if wens.laden else None
        if stroom is not None and self.zekering_rem_a:
            stroom = max(0.0, stroom - self.zekering_rem_a)
        if wens.laden or wens.herstel:
            getal(self.opties.get(CONF_TOU_STROOM), stroom)
        getal(self.opties.get(CONF_TOU_MAX_LAAD_A), wens.max_laad_a)
        getal(self.opties.get(CONF_TOU_MAX_ONTLAAD_A), wens.max_ontlaad_a)
        getal(self.opties.get(CONF_TOU_MAX_EXPORT), wens.export_w)
        for entity_id in self._lijst(CONF_TOU_LADEN):
            keuze(entity_id, "grid" if wens.laden else "uit")
        for entity_id in self._lijst(CONF_TOU_SOC):
            getal(entity_id, wens.soc)
        for entity_id in self._lijst(CONF_TOU_VERMOGEN):
            getal(entity_id, wens.vermogen_w)
        keuze(self.opties.get(CONF_TOU), "aan")
        for entity_id, tijd in zip(self._lijst(CONF_TOU_TIJD), TIJDEN):
            if self._staat(entity_id):
                waarde = eigen.get(entity_id) if wens.herstel else tijd
                if waarde:
                    uit.append((entity_id, waarde))
        return uit

    def _wijkt_af(self, entity_id: str, gewenst: object) -> bool:
        staat = self._staat(entity_id)
        if staat is None:
            return False
        domein = entity_id.split(".", 1)[0]
        if domein == "time":
            return staat.state[:5] != str(gewenst)[:5]
        if domein != "number":
            return staat.state != gewenst
        nu = _getal(staat.state)
        if nu is None:
            return True
        marge = SOC_MARGE if entity_id in self._lijst(CONF_TOU_SOC) else VERMOGEN_MARGE_W \
            if entity_id in (*self._lijst(CONF_TOU_VERMOGEN), self.opties.get(CONF_TOU_MAX_EXPORT)) else STROOM_MARGE_A
        doel = self._binnen_bereik(staat, float(gewenst))
        return abs(nu - doel) > marge or (doel == 0 and nu != 0)

    @staticmethod
    def _binnen_bereik(staat, waarde: float) -> float:
        for grens, kies in ((_getal(staat.attributes.get("min")), max), (_getal(staat.attributes.get("max")), min)):
            if grens is not None:
                waarde = kies(waarde, grens)
        return waarde

    # ------------------------------------------------------------------ Schrijven

    async def _dienst(self, entity_id: str, waarde: object) -> str | None:
        domein = entity_id.split(".", 1)[0]
        if domein == "select":
            dienst, data = "select_option", {"entity_id": entity_id, "option": waarde}
        elif domein == "switch":
            dienst, data = ("turn_on" if waarde == "on" else "turn_off"), {"entity_id": entity_id}
        elif domein == "time":
            dienst, data = "set_value", {"entity_id": entity_id, "time": str(waarde)}
        else:
            staat = self._staat(entity_id)
            data = {"entity_id": entity_id, "value": round(self._binnen_bereik(staat, float(waarde)) if staat else float(waarde))}
            dienst = "set_value"
        try:
            await asyncio.wait_for(self.hass.services.async_call(domein, dienst, data, blocking=True), TIMEOUT_S)
        except Exception as fout:
            return f"{entity_id}: {fout or type(fout).__name__}"[:120]
        return None

    def _zekering(self) -> bool:
        """Stapsgewijs: binnen 2 A van de hoofdzekering 10 A minder laden, met 5 A ruimte weer 10 A terug."""
        zekering = float(self.opties.get(CONF_EMS_ZEKERING_A) or 0)
        fase = self.fase_max()
        oud = self.zekering_rem_a
        if zekering > 0 and fase is not None:
            if fase > zekering - 2:
                self.zekering_rem_a += ZEKERING_STAP_A
            elif fase < zekering - 5 and self.zekering_rem_a > 0:
                self.zekering_rem_a = max(0.0, self.zekering_rem_a - ZEKERING_STAP_A)
        else:
            self.zekering_rem_a = 0.0
        return self.zekering_rem_a != oud

    async def zet(self, wens: ems_core.DeyeWens, forceer: bool = False) -> bool:
        """Eén ronde: bepalen wat afwijkt en dat schrijven. Hooguit eens per tien seconden, tenzij forceer."""
        nu = time.time()
        # Een andere stand (een noodstop, een nieuw blok) gaat meteen; verder hooguit eens per tien seconden.
        if not forceer and nu < self._volgende and wens.stand == self._toegepast:
            return True
        self._volgende = nu + RONDE_S
        zekering_anders = wens.laden and self._zekering()
        if wens.laden and self.zekering_rem_a and not wens.beperking:
            wens.beperking = "zekering"
        self.wens = wens
        nieuwe_stand = forceer or wens.stand != self._toegepast
        opdrachten = []
        for entity_id, gewenst in self._gewenst(wens):
            if not self._wijkt_af(entity_id, gewenst):
                continue
            vrij = nieuwe_stand or nu - self._geschreven.get(entity_id, 0) >= PER_ENTITEIT_S \
                or (zekering_anders and entity_id == self.opties.get(CONF_TOU_STROOM))
            if vrij:
                opdrachten.append((entity_id, gewenst))
        if not opdrachten:
            self._toegepast = wens.stand
            self._gelukt("niets te veranderen")
            return True
        fouten = []
        for entity_id, gewenst in opdrachten:
            if fout := await self._dienst(entity_id, gewenst):
                fouten.append(fout)
            else:
                self._geschreven[entity_id] = nu
        if fouten:
            await self._mislukt(fouten[0], len(opdrachten), wens)
            return False
        self._toegepast = wens.stand
        # Teruglezen: de Solarman-integratie meldt een nieuwe waarde soms pas bij de volgende uitlezing, dus
        # alleen loggen; een echte fout zien we aan een uitzondering of time-out.
        if afwijkend := [i for i, g in opdrachten if self._wijkt_af(i, g)]:
            _LOGGER.debug("Nog niet teruggelezen na schrijven: %s", ", ".join(afwijkend))
        self._gelukt(f"{len(opdrachten)} {'instelling' if len(opdrachten) == 1 else 'instellingen'} gezet")
        return True

    def _gelukt(self, tekst: str) -> None:
        self.laatste_opdracht = {"tijd": dt_util.utcnow().isoformat(timespec="seconds"), "resultaat": tekst}
        if self._fouten >= MAX_FOUTEN:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE)
        self._fouten = 0
        self.ems.fout = ""

    async def _mislukt(self, fout: str, aantal: int, wens: ems_core.DeyeWens) -> None:
        self._fouten += 1
        self.ems.fout = fout
        self.laatste_opdracht = {"tijd": dt_util.utcnow().isoformat(timespec="seconds"), "resultaat": f"mislukt: {fout}"}
        wens.beperking = "schrijffout"
        _LOGGER.warning("Instellingen van de omvormer zetten mislukt (%s van %s opdrachten): %s", self._fouten, aantal, fout)
        if self._fouten == MAX_FOUTEN:
            ir.async_create_issue(self.hass, DOMAIN, ISSUE, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                  translation_key=ISSUE, translation_placeholders={"fout": fout})
            if not wens.herstel and self.opgeslagen:
                # Terug naar de eigen instellingen: beter de omvormer zoals de bewoner hem had dan half gestuurd.
                await self.zet(ems_core.DeyeWens(False, self.min_pct(), None, "eigen stand", ems_core.TERUGVAL, "schrijffout",
                                                 herstel=True), forceer=True)
