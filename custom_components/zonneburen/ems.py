"""Het energieplan uitvoeren met Home Assistant.

De server zet het plan retained op hub/plan. Elke tik (5 s) regelt de uitvoerder de batterij bij via
een number-entiteit van de merkintegratie: het vermogen van de batterij, of bij een Victron ESS het
netdoel. Een select-entiteit kan de batterij eerst in de stand voor externe sturing zetten en hem bij
het stoppen terugzetten. Zonder geldig plan, bij een pauze of zonder verse netmeting krijgt de
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
from .const import (
    CONF_BEDIENING, CONF_EMS_AAN, CONF_EMS_LADEN_POSITIEF, CONF_EMS_MAX_LAAD_W, CONF_EMS_MAX_ONTLAAD_W, CONF_EMS_MODUS,
    CONF_EMS_MODUS_STUREN, CONF_EMS_MODUS_TERUG, CONF_EMS_NETDOEL, CONF_EMS_VERMOGEN, CONF_EMS_ZEKERING_A, CONF_NET, DOMAIN,
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
        self.ruw: str = ""
        self.geweigerd = ""
        self.fout = ""
        self.pauze_tot = 0.0
        self.laatste: ems_core.Opdracht | None = None
        self.gestart = False
        self._vorig_bat: float | None = None
        self._laatst_geschreven: float | None = None
        self._laatst_geschreven_op = 0.0
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

    def mag_sturen(self) -> bool:
        return bool(self.opties.get(CONF_EMS_AAN) and self.opties.get(CONF_BEDIENING, True) and self.opties.get(CONF_EMS_VERMOGEN))

    def niveau(self) -> str:
        if not self.mag_sturen():
            return "D"
        return "B" if self._traag() else "A"

    def _traag(self) -> bool:
        staat = self.hub.hass.states.get(self.opties.get(CONF_NET) or "")
        if not staat:
            return True
        laatst = getattr(staat, "last_reported", None) or staat.last_updated
        return (dt_util.utcnow() - laatst).total_seconds() > TRAAG_S

    def vermogens(self) -> dict:
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

    # ------------------------------------------------------------------ Plan

    def ontvang(self, payload: bytes) -> None:
        tekst = payload.decode("utf-8", "replace") if payload else ""
        if not tekst:
            if self.plan:
                self.hub._log(0, "Energieplan uitgezet in de webapp")
            self.plan, self.ruw, self.geweigerd = None, "", ""
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
        grenzen = ems_core.Grenzen(
            float(self.opties.get(CONF_EMS_MAX_LAAD_W) or 0), float(self.opties.get(CONF_EMS_MAX_ONTLAAD_W) or 0),
            float(self.opties.get(CONF_EMS_ZEKERING_A) or 0), self.pauze_tot > nu, self.hub.noodstop)
        opdracht = ems_core.regel(self.plan, nu, meting, grenzen, self._vorig_bat, self._traag())

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
            self._stuur_status(opdracht, meting)

    async def _dienst(self, domein: str, dienst: str, data: dict) -> bool:
        try:
            await self.hub.hass.services.async_call(domein, dienst, data, blocking=True)
        except Exception as fout:  # noqa: BLE001 - de service van een merkintegratie kan van alles gooien
            self.fout = f"{data.get('entity_id')}: {fout}"[:120]
            return False
        return True

    async def _modus(self, optie_sleutel: str) -> bool:
        entiteit, optie = self.opties.get(CONF_EMS_MODUS), self.opties.get(optie_sleutel)
        if not entiteit or not optie:
            return True
        domein = entiteit.split(".", 1)[0]
        return await self._dienst(domein, "select_option", {"entity_id": entiteit, "option": optie})

    async def _start(self) -> None:
        if await self._modus(CONF_EMS_MODUS_STUREN):
            self.gestart, self.fout, self._vorig_bat, self._laatst_geschreven = True, "", None, None
            self.hub._log(1, f"Energieplan stuurt nu {self.opties.get(CONF_EMS_VERMOGEN)}")
        else:
            self._volgende_start = time.time() + HERSTART_S
        self.hub._stuur_vermogens()

    async def _stop(self, waarom: str) -> None:
        await self._schrijf(0, True)
        await self._modus(CONF_EMS_MODUS_TERUG)
        self.gestart, self._vorig_bat, self._laatst_geschreven = False, None, None
        self.hub._log(0, f"Energieplan: batterij weer in eigen stand ({waarom})")

    async def _zet(self, opdracht: ems_core.Opdracht) -> bool:
        if self.opties.get(CONF_EMS_NETDOEL):
            waarde = round(opdracht.net_doel_w)
        else:
            waarde = -opdracht.bat_w if self.opties.get(CONF_EMS_LADEN_POSITIEF) else opdracht.bat_w
        return await self._schrijf(waarde)

    async def _schrijf(self, waarde: float, altijd: bool = False) -> bool:
        nu = time.time()
        if not altijd and self._laatst_geschreven is not None and abs(waarde - self._laatst_geschreven) < DREMPEL_W \
                and nu - self._laatst_geschreven_op < VERVERS_S:
            return True
        entiteit = self.opties.get(CONF_EMS_VERMOGEN)
        if not entiteit:
            return False
        if await self._dienst(entiteit.split(".", 1)[0], "set_value", {"entity_id": entiteit, "value": round(waarde)}):
            self._laatst_geschreven, self._laatst_geschreven_op, self.fout = waarde, nu, ""
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
               "bron": self.laatste.bron if self.laatste else ems_core.TERUGVAL}
        if self.pauze_tot > nu:
            uit["pauzeTot"] = int(self.pauze_tot)
        if self.geweigerd:
            uit["geweigerd"] = self.geweigerd
        if self.fout:
            uit["fout"] = self.fout
        return uit
