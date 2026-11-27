"""Een Deye of Sunsynk sturen via zijn tijdprogramma's (Time of Use).

Zo'n omvormer kent geen vermogens-setpoint in W. Wat wel kan, via bijvoorbeeld de Solarman-integratie:
zes tijdprogramma's met elk een keuze om van het net te laden (Grid of Disabled) en een lading (SOC) als
ondergrens of doel, plus een laadstroom van het net in ampère. Zonneburen zet de zes programma's op vaste
tijden (00:00, 04:00 ... 20:00) en geeft ze allemaal dezelfde opdracht: zo doet de omvormer de hele dag
wat het kwartier van nu vraagt.

Alleen schrijven wat afwijkt van wat de omvormer nu meldt, hooguit één ronde per minuut, elke opdracht met
een time-out. Drie mislukte rondes op rij: een melding bij Reparaties.
"""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from . import ems_core
from .const import (
    CONF_TOU,
    CONF_TOU_LADEN,
    CONF_TOU_MAX_PCT,
    CONF_TOU_MIN_PCT,
    CONF_TOU_MIN_SOC,
    CONF_TOU_SOC,
    CONF_TOU_SPANNING,
    CONF_TOU_STROOM,
    CONF_TOU_TIJD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

RONDE_S = 60
TIMEOUT_S = 10
SOC_MARGE = 2
STROOM_MARGE_A = 5
MAX_FOUTEN = 3
TIJDEN = ("00:00:00", "04:00:00", "08:00:00", "12:00:00", "16:00:00", "20:00:00")
ISSUE = "deye_schrijven"


def _getal(waarde) -> float | None:
    try:
        return float(waarde)
    except (TypeError, ValueError):
        return None


def optie(opties: list[str], soort: str) -> str | None:
    """De optie voor laden van het net ("Grid"), uit ("Disabled") of aan ("Enabled") in de lijst van de omvormer."""
    plat = {o: o.lower() for o in opties}
    if soort == "grid":
        return next((o for o, p in plat.items() if p == "grid"), None) \
            or next((o for o, p in plat.items() if "grid" in p and "gen" not in p), None) \
            or next((o for o, p in plat.items() if "grid" in p), None)
    if soort == "uit":
        return next((o for o, p in plat.items() if p in ("disabled", "disable", "none", "off", "uit")), None) \
            or next((o for o, p in plat.items() if "disab" in p or p.startswith("no")), None)
    if soort == "aan":
        return next((o for o, p in plat.items() if p in ("enabled", "enable", "on", "aan")), None) \
            or next((o for o, p in plat.items() if "enab" in p and "disab" not in p), None)
    return None


class DeyeSturing:
    def __init__(self, ems) -> None:
        self.ems = ems
        self._volgende = 0.0
        self._fouten = 0
        self.laatste_opdracht: dict | None = None
        self.wens: ems_core.DeyeWens | None = None

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

    # ------------------------------------------------------------------ Uit Home Assistant

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

    def max_stroom(self) -> float | None:
        staat = self.hass.states.get(self.opties.get(CONF_TOU_STROOM) or "")
        return _getal(staat.attributes.get("max")) if staat else None

    # ------------------------------------------------------------------ Schrijven

    async def _dienst(self, domein: str, dienst: str, data: dict) -> str | None:
        try:
            await asyncio.wait_for(self.hass.services.async_call(domein, dienst, data, blocking=True), TIMEOUT_S)
        except Exception as fout:  # noqa: BLE001 - een merkintegratie kan van alles gooien, ook een time-out
            return f"{data.get('entity_id')}: {fout or type(fout).__name__}"[:120]
        return None

    def _opdrachten(self, wens: ems_core.DeyeWens) -> list[tuple[str, str, dict]]:
        """Alleen wat afwijkt van wat de omvormer nu meldt."""
        uit: list[tuple[str, str, dict]] = []
        tou = self._staat(self.opties.get(CONF_TOU))
        if tou is not None:
            aan = optie([str(o) for o in tou.attributes.get("options") or []], "aan")
            if aan and tou.state != aan:
                uit.append(("select", "select_option", {"entity_id": tou.entity_id, "option": aan}))
        for entity_id, tijd in zip(self._lijst(CONF_TOU_TIJD), TIJDEN):
            staat = self._staat(entity_id)
            if staat is not None and staat.state[:5] != tijd[:5]:
                uit.append(("time", "set_value", {"entity_id": entity_id, "time": tijd}))
        for entity_id in self._lijst(CONF_TOU_LADEN):
            staat = self._staat(entity_id)
            if staat is None:
                continue
            gewenst = optie([str(o) for o in staat.attributes.get("options") or []], "grid" if wens.laden else "uit")
            if gewenst and staat.state != gewenst:
                uit.append(("select", "select_option", {"entity_id": entity_id, "option": gewenst}))
        for entity_id in self._lijst(CONF_TOU_SOC):
            staat = self._staat(entity_id)
            if staat is None:
                continue
            doel = wens.soc
            for grens, kies in ((_getal(staat.attributes.get("min")), max), (_getal(staat.attributes.get("max")), min)):
                if grens is not None:
                    doel = kies(doel, grens)
            if (nu := _getal(staat.state)) is None or abs(nu - doel) > SOC_MARGE:
                uit.append(("number", "set_value", {"entity_id": entity_id, "value": round(doel)}))
        stroom = self._staat(self.opties.get(CONF_TOU_STROOM))
        if wens.laden and wens.stroom_a and stroom is not None:
            doel = wens.stroom_a
            for grens, kies in ((_getal(stroom.attributes.get("min")), max), (_getal(stroom.attributes.get("max")), min)):
                if grens is not None:
                    doel = kies(doel, grens)
            if (nu := _getal(stroom.state)) is None or abs(nu - doel) > STROOM_MARGE_A:
                uit.append(("number", "set_value", {"entity_id": stroom.entity_id, "value": round(doel)}))
        return uit

    async def zet(self, wens: ems_core.DeyeWens, forceer: bool = False) -> bool:
        """Eén schrijfronde. Hooguit één per minuut, tenzij forceer (bij het stoppen)."""
        self.wens = wens
        nu = time.time()
        if not forceer and nu < self._volgende:
            return True
        self._volgende = nu + RONDE_S
        opdrachten = self._opdrachten(wens)
        if not opdrachten:
            self._gelukt("niets te veranderen")
            return True
        fouten = [f for domein, dienst, data in opdrachten if (f := await self._dienst(domein, dienst, data))]
        if fouten:
            self._mislukt(fouten[0], len(opdrachten))
            return False
        self._gelukt(f"{len(opdrachten)} {'instelling' if len(opdrachten) == 1 else 'instellingen'} gezet")
        return True

    def _gelukt(self, tekst: str) -> None:
        self.laatste_opdracht = {"tijd": dt_util.utcnow().isoformat(timespec="seconds"), "resultaat": tekst}
        if self._fouten >= MAX_FOUTEN:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE)
        self._fouten = 0
        self.ems.fout = ""

    def _mislukt(self, fout: str, aantal: int) -> None:
        self._fouten += 1
        self.ems.fout = fout
        self.laatste_opdracht = {"tijd": dt_util.utcnow().isoformat(timespec="seconds"), "resultaat": f"mislukt: {fout}"}
        _LOGGER.warning("Tijdprogramma's van de omvormer zetten mislukt (%s van %s opdrachten): %s", self._fouten, aantal, fout)
        if self._fouten == MAX_FOUTEN:
            ir.async_create_issue(self.hass, DOMAIN, ISSUE, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                  translation_key=ISSUE, translation_placeholders={"fout": fout})
