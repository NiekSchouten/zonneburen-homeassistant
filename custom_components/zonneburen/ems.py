"""Het energieplan uitvoeren met Home Assistant.

De server zet het plan retained op hub/plan. Elke tik (5 s) regelt de uitvoerder de batterij bij via
een number-entiteit van de merkintegratie: het vermogen van de batterij, of bij een Victron ESS het
netdoel. Heeft de batterij aparte getallen voor laden en ontladen (Marstek, SolarEdge), dan zet een
select-entiteit de richting. Een select of schakelaar kan de batterij eerst in de stand voor externe
sturing zetten en hem bij het stoppen terugzetten. Zonder geldig plan, bij een pauze of zonder verse netmeting krijgt de
batterij zijn eigen stand terug.
"""

from __future__ import annotations

import json
import logging
import time

from homeassistant.components import persistent_notification
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from . import ems_core
from .deye import DeyeSturing
from .const import (
    CONF_EMS_PROFIEL, CONF_TOU, PROFIEL_DEYE,
    CONF_BEDIENING, CONF_EMS_AAN, CONF_EMS_LADEN_POSITIEF, CONF_EMS_MAX_LAAD_W, CONF_EMS_MAX_ONTLAAD_W, CONF_EMS_MODUS,
    CONF_EMS_MODUS_STUREN, CONF_EMS_MODUS_TERUG, CONF_EMS_NETDOEL, CONF_EMS_ONTLAAD, CONF_EMS_RICHTING, CONF_EMS_RICHTING_LADEN,
    CONF_EMS_RICHTING_ONTLADEN, CONF_EMS_RICHTING_STOP, CONF_EMS_VERMOGEN, CONF_EMS_ZEKERING_A, CONF_NET, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DREMPEL_W = 50
VERVERS_S = 30
STATUS_S = 15 * 60
TRAAG_S = 20
HERSTART_S = 60

# Integraties die zelf een batterij plannen: twee regelaars werken elkaar tegen.
ANDERE_EMS = ("emhass", "evcc", "predbat", "batterij_optimalisatie")


class EmsUitvoerder:
    def __init__(self, hub) -> None:
        self.hub = hub
        self.plan: ems_core.Plan | None = None
        # Het plan hiervoor: een nieuw plan begint pas bij het volgende kwartier, tot dan geldt dit.
        self.vorig_plan: ems_core.Plan | None = None
        self.deye = DeyeSturing(self)
        self.ruw: str = ""
        # Het schema voor de kaarten (protocol.lees_schema): ook bij Adviseren, als er geen plan is om te sturen.
        self.schema: dict | None = None
        # De laatste meting (net, batterij, lading), voor de kaart Batterij nu.
        self.meting: ems_core.Meting | None = None
        self.geweigerd = ""
        self.fout = ""
        self.pauze_tot = 0.0
        self.laatste: ems_core.Opdracht | None = None
        self.gestart = False
        self._vorig_bat: float | None = None
        # Per entiteit wat er het laatst is geschreven en wanneer: niet elke tik dezelfde waarde sturen.
        self._geschreven: dict[str, tuple[float, float]] = {}
        self._richting: str | None = None
        self._volgende_start = 0.0
        self._status_op = 0.0
        self._status_klaar = True
        self._andere_ems_gemeld = False
        self._bezig = False
        self._vermogens_gemeld = ""

    # ------------------------------------------------------------------ Instellingen

    @property
    def opties(self) -> dict:
        return self.hub.opties

    def is_deye(self) -> bool:
        return self.opties.get(CONF_EMS_PROFIEL) == PROFIEL_DEYE

    def mag_sturen(self) -> bool:
        if not (self.opties.get(CONF_EMS_AAN) and self.opties.get(CONF_BEDIENING, True)):
            return False
        return self.deye.klaar() if self.is_deye() else bool(self.opties.get(CONF_EMS_VERMOGEN))

    def apparaat(self, index: int) -> ems_core.ApparaatStand:
        """Wat het plan nu van een apparaat vraagt. Niets als uitvoeren uit staat of het plan gepauzeerd is.

        Een batterij is hier niet voor nodig: ook een huis met alleen een boiler of auto kan het plan volgen."""
        nu = time.time()
        if not (self.opties.get(CONF_EMS_AAN) and self.opties.get(CONF_BEDIENING, True)) or self.pauze_tot > nu:
            return ems_core.ApparaatStand(None)
        return ems_core.apparaat_stand(self.plan, index, nu, self.vorig_plan)

    def niveau(self) -> str:
        if not self.mag_sturen():
            return "D"
        # Een Deye volgt zelf zijn CT-meter; de integratie geeft per kwartier de stand.
        if self.is_deye():
            return "A"
        return "B" if self._traag() else "A"

    def _traag(self) -> bool:
        staat = self.hub.hass.states.get(self.opties.get(CONF_NET) or "")
        if not staat:
            return True
        laatst = getattr(staat, "last_reported", None) or staat.last_updated
        return (dt_util.utcnow() - laatst).total_seconds() > TRAAG_S

    def vermogens(self) -> dict:
        if self.is_deye():
            # Een Deye regelt zelf op zijn CT-meter: per kwartier de goede stand is genoeg voor volledig sturen.
            # Naar het net ontladen (Export First) alleen als de bewoner dat toestaat; anders plant de server het niet.
            standen = [s for s in ems_core.STANDEN if s != ems_core.NETONTLADEN or self.deye.terugleveren()]
            bericht = {"v": 1, "uitvoerder": "homeassistant", "adapter": self.opties.get(CONF_TOU) or "deye_tou",
                       "niveau": self.niveau(), "standen": standen if self.mag_sturen() else [], "reactieMs": 10000,
                       "profiel": PROFIEL_DEYE, "kanVastOntladen": self.deye.terugleveren(), "regeltZelfOpMeter": True,
                       "minSoc": round(self.deye.min_pct()), "maxSocNet": round(self.deye.max_pct()),
                       "maxLaadW": self.deye.max_laad_w(), "maxOntlaadW": self.deye.max_ontlaad_w()}
            if not self.deye.klaar():
                bericht["fout"] = "kies in de integratie de tijdprogramma's van je omvormer"
            elif not self.opties.get(CONF_BEDIENING, True):
                bericht["fout"] = "bediening op afstand staat uit in Home Assistant"
            elif not self.opties.get(CONF_EMS_AAN):
                bericht["fout"] = "energieplan uitvoeren staat uit in Home Assistant"
            elif self.fout:
                bericht["fout"] = self.fout
            return bericht
        bericht = {"v": 1, "uitvoerder": "homeassistant", "adapter": self.opties.get(CONF_EMS_VERMOGEN) or "",
                   "niveau": self.niveau(), "standen": list(ems_core.STANDEN) if self.mag_sturen() else [],
                   "reactieMs": 5000}
        for sleutel, veld in ((CONF_EMS_MAX_LAAD_W, "maxLaadW"), (CONF_EMS_MAX_ONTLAAD_W, "maxOntlaadW")):
            if waarde := self.opties.get(sleutel):
                bericht[veld] = waarde
        if not self.opties.get(CONF_EMS_VERMOGEN):
            bericht["fout"] = "kies in de integratie de entiteit die de batterij stuurt"
        elif not self.opties.get(CONF_BEDIENING, True):
            bericht["fout"] = "bediening op afstand staat uit in Home Assistant"
        elif not self.opties.get(CONF_EMS_AAN):
            bericht["fout"] = "energieplan uitvoeren staat uit in Home Assistant"
        elif self.fout:
            bericht["fout"] = self.fout
        return bericht

    async def async_stop(self, afsluiten: bool) -> None:
        """Bij herladen of verwijderen de eigen instellingen terug. Bij het afsluiten van Home Assistant niet:
        dan stuurt de integratie na de herstart gewoon verder, en schrijven we niet twee keer voor niets."""
        if self.is_deye() and self.gestart and not afsluiten:
            await self.deye.herstel(wissen=True)
            self.gestart = False

    def _beperking(self) -> str:
        if self.pauze_tot > time.time():
            return "pauze"
        if not self.hub.verbinding.verbonden:
            return "geen verbinding"
        if self.is_deye() and self.deye.wens and self.deye.wens.beperking:
            return self.deye.wens.beperking.replace("_", " ")
        return self.laatste.afwijking if self.laatste and self.laatste.afwijking else ""

    # ------------------------------------------------------------------ Plan

    def ontvang(self, payload: bytes) -> None:
        tekst = payload.decode("utf-8", "replace") if payload else ""
        if not tekst:
            if self.plan:
                self.hub._log(0, "Energieplan uitgezet in de webapp")
            self.plan, self.vorig_plan, self.ruw, self.geweigerd = None, None, "", ""
            self._status_klaar = True
            return
        try:
            bericht = json.loads(tekst)
        except ValueError:
            self.geweigerd = "geen geldige JSON"
        else:
            plan, fout = ems_core.lees_plan(bericht)
            if fout:
                self.geweigerd = fout
                self.hub._log(2, f"Energieplan geweigerd: {fout}")
            else:
                if self.plan is not None and self.plan.plan_id != plan.plan_id:
                    self.vorig_plan = self.plan
                self.plan, self.ruw, self.geweigerd = plan, tekst, ""
        self._status_klaar = True
        self.hub._bijgewerkt()

    def herstel(self, ruw: str | None) -> None:
        if ruw:
            self.ontvang(ruw.encode())

    def pauzeer(self, seconden: float) -> None:
        self.pauze_tot = time.time() + seconden if seconden else 0
        self.hub._log(0, "Energieplan gepauzeerd in Home Assistant" if seconden else "Energieplan weer volgens plan")
        self._status_klaar = True
        self.hub._bijgewerkt()

    # ------------------------------------------------------------------ Regelen

    def _leeftijd(self) -> float:
        staat = self.hub.hass.states.get(self.opties.get(CONF_NET) or "")
        if not staat or staat.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return 1e9
        laatst = getattr(staat, "last_reported", None) or staat.last_updated
        return (dt_util.utcnow() - laatst).total_seconds()

    async def tik(self, net: float | None, bat: float | None, soc: float | None) -> None:
        # Een trage merkintegratie mag de volgende tik niet laten inhalen.
        if self._bezig:
            return
        self._bezig = True
        try:
            await self._tik(net, bat, soc)
        finally:
            self._bezig = False

    async def _tik(self, net: float | None, bat: float | None, soc: float | None) -> None:
        nu = time.time()
        meting = ems_core.Meting(net, bat, soc, self._leeftijd() if net is not None else 1e9)
        self.meting = meting
        grenzen = ems_core.Grenzen(
            float(self.opties.get(CONF_EMS_MAX_LAAD_W) or 0), float(self.opties.get(CONF_EMS_MAX_ONTLAAD_W) or 0),
            float(self.opties.get(CONF_EMS_ZEKERING_A) or 0), self.pauze_tot > nu, self.hub.noodstop)
        if self.is_deye():
            await self._tik_deye(nu, meting, grenzen)
            return
        opdracht = ems_core.regel(self.plan, nu, meting, grenzen, self._vorig_bat, self._traag(), self.vorig_plan)

        sturen = self.mag_sturen() and (
            opdracht.bron == ems_core.PLAN or (opdracht.bron == ems_core.GRENS and opdracht.slot >= 0 and meting.leeftijd_s < 60))
        if sturen:
            self._controleer_andere_ems()
            if not self.gestart and nu >= self._volgende_start:
                await self._start()
            if self.gestart:
                if await self._zet(opdracht):
                    self._vorig_bat = opdracht.bat_w
        elif self.gestart:
            await self._stop(opdracht.afwijking or "geen plan")

        # Instellingen in Home Assistant veranderd: de server moet weten wat deze uitvoerder nu kan.
        if self.hub.verbinding.verbonden and json.dumps(self.vermogens(), sort_keys=True) != self._vermogens_gemeld:
            self.hub._stuur_vermogens()

        vorige = self.laatste
        self.laatste = opdracht
        if (vorige is None or vorige.stand != opdracht.stand or vorige.bron != opdracht.bron or vorige.slot != opdracht.slot
                or nu - self._status_op > STATUS_S):
            self._status_klaar = True
        if self._status_klaar:
            self.hub._bijgewerkt()
            self._stuur_status(opdracht, meting)

    async def _tik_deye(self, nu: float, meting: ems_core.Meting, grenzen: ems_core.Grenzen) -> None:
        """Een Deye via zijn tijdprogramma's: geen bijregelen op de seconde, wel elk kwartier de goede opdracht."""
        # De hoofdzekering regelt de uitvoerder zelf, stapsgewijs: geen fasestroom hier.
        wens = ems_core.deye_wens(self.plan, self.vorig_plan, nu, meting.soc, self.deye.spanning(), grenzen,
                                  self.deye.max_pct(), self.deye.min_pct(), self.deye.max_stroom(), None,
                                  self.deye.max_laad_w(), self.deye.max_ontlaad_w(), self.deye.terugleveren())
        if self.mag_sturen():
            self._controleer_andere_ems()
            if not self.gestart:
                # Eerst de instellingen van de bewoner bewaren: die gaan terug bij uitzetten.
                await self.deye.bewaar_eigen_stand()
                self.gestart, self.fout = True, ""
                self.hub._log(1, "Energieplan stuurt nu je omvormer (tijdprogramma's); je eigen instellingen zijn bewaard")
                self.hub._stuur_vermogens()
            vorige = self.deye.wens
            await self.deye.zet(wens)
            if vorige is not None and vorige.stand != wens.stand:
                self.hub._log(0, f"Batterij: {ems_core.ZINNEN.get(wens.stand, wens.stand).lower()}"
                                 + (f" ({wens.beperking.replace('_', ' ')})" if wens.beperking else ""))
        elif self.gestart:
            await self.deye.herstel(wissen=True)
            self.gestart = False
            self.hub._log(0, "Energieplan: je omvormer staat weer op je eigen instellingen")

        if self.hub.verbinding.verbonden and json.dumps(self.vermogens(), sort_keys=True) != self._vermogens_gemeld:
            self.hub._stuur_vermogens()
        opdracht = ems_core.Opdracht(wens.stand, wens.bron if self.gestart else ems_core.TERUGVAL, wens.gevraagd_w, 0, wens.slot,
                                     wens.beperking.replace("_", " "))
        vorige_opdracht = self.laatste
        self.laatste = opdracht
        if (vorige_opdracht is None or vorige_opdracht.stand != opdracht.stand or vorige_opdracht.slot != opdracht.slot
                or nu - self._status_op > STATUS_S):
            self._status_klaar = True
        if self._status_klaar:
            self.hub._bijgewerkt()
            self._stuur_status(opdracht, meting)

    async def _dienst(self, domein: str, dienst: str, data: dict) -> bool:
        try:
            await self.hub.hass.services.async_call(domein, dienst, data, blocking=True)
        except Exception as fout:  # noqa: BLE001 - de service van een merkintegratie kan van alles gooien
            self.fout = f"{data.get('entity_id')}: {fout}"[:120]
            return False
        return True

    async def _modus(self, sturen: bool) -> bool:
        """De batterij in de stand voor externe sturing zetten, of terug in zijn eigen stand."""
        entiteit = self.opties.get(CONF_EMS_MODUS)
        if not entiteit:
            return True
        domein = entiteit.split(".", 1)[0]
        if domein in ("switch", "input_boolean"):
            return await self._dienst(domein, "turn_on" if sturen else "turn_off", {"entity_id": entiteit})
        optie = self.opties.get(CONF_EMS_MODUS_STUREN if sturen else CONF_EMS_MODUS_TERUG)
        if not optie:
            return True
        return await self._dienst(domein, "select_option", {"entity_id": entiteit, "option": optie})

    def _apart(self) -> bool:
        return bool(self.opties.get(CONF_EMS_ONTLAAD))

    async def _start(self) -> None:
        if await self._modus(True):
            self.gestart, self.fout, self._vorig_bat, self._geschreven, self._richting = True, "", None, {}, None
            self.hub._log(1, f"Energieplan stuurt nu {self.opties.get(CONF_EMS_VERMOGEN)}")
        else:
            self._volgende_start = time.time() + HERSTART_S
        self.hub._stuur_vermogens()

    async def _stop(self, waarom: str) -> None:
        if self._apart():
            await self._zet_richting(CONF_EMS_RICHTING_STOP)
            await self._schrijf(0, True, self.opties.get(CONF_EMS_ONTLAAD))
        await self._schrijf(0, True)
        await self._modus(False)
        self.gestart, self._vorig_bat, self._geschreven, self._richting = False, None, {}, None
        self.hub._log(0, f"Energieplan: batterij weer in eigen stand ({waarom})")

    async def _zet(self, opdracht: ems_core.Opdracht) -> bool:
        if self._apart():
            return await self._zet_apart(opdracht.bat_w)
        if self.opties.get(CONF_EMS_NETDOEL):
            waarde = round(opdracht.net_doel_w)
        else:
            waarde = -opdracht.bat_w if self.opties.get(CONF_EMS_LADEN_POSITIEF) else opdracht.bat_w
        return await self._schrijf(waarde)

    async def _zet_apart(self, bat_w: float) -> bool:
        """Laden en ontladen elk met een eigen getal (allebei positief), en de richting via een keuze."""
        laden = -bat_w if bat_w <= -DREMPEL_W else 0
        ontladen = bat_w if bat_w >= DREMPEL_W else 0
        richting = CONF_EMS_RICHTING_LADEN if laden else CONF_EMS_RICHTING_ONTLADEN if ontladen else CONF_EMS_RICHTING_STOP
        ontlaad = self.opties.get(CONF_EMS_ONTLAAD)
        # Eerst wat uit moet, dan de richting, dan wat aan moet: nooit even beide kanten op.
        gelukt = True
        if not laden:
            gelukt &= await self._schrijf(0)
        if not ontladen:
            gelukt &= await self._schrijf(0, entiteit=ontlaad)
        gelukt &= await self._zet_richting(richting)
        if laden:
            gelukt &= await self._schrijf(laden)
        if ontladen:
            gelukt &= await self._schrijf(ontladen, entiteit=ontlaad)
        return gelukt

    async def _zet_richting(self, sleutel: str) -> bool:
        entiteit, optie = self.opties.get(CONF_EMS_RICHTING), self.opties.get(sleutel)
        if not entiteit or not optie or optie == self._richting:
            return True
        if await self._dienst(entiteit.split(".", 1)[0], "select_option", {"entity_id": entiteit, "option": optie}):
            self._richting = optie
            return True
        return False

    async def _schrijf(self, waarde: float, altijd: bool = False, entiteit: str | None = None) -> bool:
        entiteit = entiteit or self.opties.get(CONF_EMS_VERMOGEN)
        if not entiteit:
            return False
        # Binnen wat de entiteit zelf toestaat: anders weigert de merkintegratie de hele opdracht.
        if staat := self.hub.hass.states.get(entiteit):
            laag, hoog = staat.attributes.get("min"), staat.attributes.get("max")
            if isinstance(hoog, (int, float)):
                waarde = min(waarde, hoog)
            if isinstance(laag, (int, float)):
                waarde = max(waarde, laag)
        nu = time.time()
        vorig = self._geschreven.get(entiteit)
        if not altijd and vorig is not None and abs(waarde - vorig[0]) < DREMPEL_W and nu - vorig[1] < VERVERS_S:
            return True
        if await self._dienst(entiteit.split(".", 1)[0], "set_value", {"entity_id": entiteit, "value": round(waarde)}):
            self._geschreven[entiteit] = (waarde, nu)
            self.fout = ""
            return True
        return False

    def _controleer_andere_ems(self) -> None:
        if self._andere_ems_gemeld:
            return
        andere = [d for d in ANDERE_EMS if self.hub.hass.config_entries.async_entries(d)]
        if andere:
            self._andere_ems_gemeld = True
            tekst = (f"Naast Zonneburen stuurt mogelijk ook {', '.join(andere)} je batterij. Twee regelaars werken elkaar tegen: "
                     "zet er één uit, of zet in Zonneburen 'Energieplan uitvoeren' uit.")
            self.hub._log(3, tekst[:120])
            persistent_notification.async_create(self.hub.hass, tekst, title="Zonneburen", notification_id=f"{DOMAIN}_andere_ems")

    # ------------------------------------------------------------------ Melden

    def _stuur_status(self, opdracht: ems_core.Opdracht, meting: ems_core.Meting) -> None:
        if not self.hub.verbinding.verbonden:
            return
        bericht = {"stand": opdracht.stand, "bron": opdracht.bron if self.gestart else ems_core.TERUGVAL}
        if self.plan:
            bericht["planId"] = self.plan.plan_id
        if opdracht.slot >= 0 and self.plan:
            bericht["slot"] = self.plan.t0 + opdracht.slot * self.plan.stap
        for veld, waarde in (("netW", meting.net_w), ("batW", meting.bat_w), ("soc", meting.soc)):
            if waarde is not None:
                bericht[veld] = round(waarde, 1) if veld == "soc" else round(waarde)
        if self.fout or opdracht.afwijking:
            bericht["afwijking"] = (self.fout or opdracht.afwijking)[:60]
        if self.geweigerd:
            bericht["geweigerd"] = self.geweigerd
        self.hub._publiceer_huis("hub/planstatus", json.dumps(bericht, ensure_ascii=False))
        self._status_klaar = False
        self._status_op = time.time()

    def info(self) -> dict:
        nu = time.time()
        uit = {"plan": self.plan is not None, "actief": self.mag_sturen(), "gestart": self.gestart,
               "stand": self.laatste.stand if self.laatste else ems_core.ZELFVERBRUIK,
               "bron": self.laatste.bron if self.laatste else ems_core.TERUGVAL,
               # Voert Home Assistant de blokken voor apparaten uit? Daar is geen batterij voor nodig.
               "apparaten": bool(self.opties.get(CONF_EMS_AAN) and self.opties.get(CONF_BEDIENING, True))}
        if self.pauze_tot > nu:
            uit["pauzeTot"] = int(self.pauze_tot)
        if self.geweigerd:
            uit["geweigerd"] = self.geweigerd
        if self.fout:
            uit["fout"] = self.fout
        # Terugkoppeling: wat het plan vraagt, wat er echt is gezet, en waarom het eventueel minder is.
        if self.laatste is not None:
            uit["gevraagd_vermogen_w"] = round(self.laatste.bat_w)
            uit["toegepaste_stand"] = self.laatste.stand if self.gestart else "eigen stand"
            if beperking := self._beperking():
                uit["beperking"] = beperking
        if self.is_deye() and self.deye.laatste_opdracht:
            uit["laatste_opdracht"] = self.deye.laatste_opdracht
        if (m := self.meting) is not None:
            uit.update({k: round(v, 1) if k == "soc_nu" else round(v) for k, v in
                        (("soc_nu", m.soc), ("batterij_w", m.bat_w), ("net_w", m.net_w)) if v is not None})
        return uit
