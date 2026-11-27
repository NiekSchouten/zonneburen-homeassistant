"""Klaar om: wanneer een wasmachine, vaatwasser of droger klaar moet zijn als je hem slim start."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ZonneburenConfigEntry, apparaat_regels
from .const import SIGNAAL_BIJGEWERKT
from .entiteit import ZonneburenEntiteit

STANDAARD_UREN = 8


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    bekend: set[int] = set()

    @callback
    def nieuwe() -> None:
        if nieuw := sorted(programma_apparaten(hub) - bekend):
            bekend.update(nieuw)
            voeg_toe([KlaarOm(hub, i) for i in nieuw])

    nieuwe()
    entry.async_on_unload(async_dispatcher_connect(hass, f"{SIGNAAL_BIJGEWERKT}_{entry.entry_id}", nieuwe))


def programma_apparaten(hub) -> set[int]:
    """Apparaten die het plan als wasmachine, vaatwasser of droger kent."""
    return {i for i, a in enumerate(hub._apparaten()) if a["enabled"] and a["host"]
            and hub.ems.soort(i).get("soort") == apparaat_regels.PLAN_PROGRAMMA}


def naam(hub, i: int) -> str:
    return hub._apparaten()[i]["name"] or f"apparaat {i + 1}"


class KlaarOm(ZonneburenEntiteit, DateTimeEntity):
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, hub, index: int) -> None:
        super().__init__(hub, f"klaar_om_{index}")
        self.index = index
        self._attr_translation_key = None
        self._attr_name = f"{naam(hub, index)} klaar om"

    @property
    def native_value(self) -> datetime:
        if (klaar := self.hub.sturing.klaar_om.get(self.index)) is not None:
            return dt_util.utc_from_timestamp(klaar)
        # Nog niets gekozen: over acht uur, op een heel kwartier.
        t = dt_util.utcnow() + timedelta(hours=STANDAARD_UREN)
        return t.replace(minute=t.minute // 15 * 15, second=0, microsecond=0)

    async def async_set_value(self, value: datetime) -> None:
        klaar = value.timestamp()
        self.hub.sturing.klaar_om[self.index] = klaar
        self.hub.sturing.bewaar()
        # Al klaargezet? Dan geldt de nieuwe eindtijd meteen.
        if self.index in self.hub.sturing.klaargezet:
            await self.hub.klaarzetten(self.index, klaar)
        self.async_write_ha_state()
