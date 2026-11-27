"""Snelknoppen voor het energieplan: twee uur pauze, of weer volgens plan."""

from __future__ import annotations

import time

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZonneburenConfigEntry
from .const import SIGNAAL_BIJGEWERKT
from .datetime import STANDAARD_UREN, naam, programma_apparaten
from .entiteit import ZonneburenEntiteit

PAUZE_S = 2 * 3600


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    voeg_toe([EnergieplanPauze(hub), EnergieplanHervat(hub)])

    # Per wasmachine, vaatwasser of droger: slim starten en weer annuleren.
    bekend: set[int] = set()

    @callback
    def nieuwe() -> None:
        if nieuw := sorted(programma_apparaten(hub) - bekend):
            bekend.update(nieuw)
            voeg_toe([k for i in nieuw for k in (StartSlim(hub, i), StopSlim(hub, i))])

    nieuwe()
    entry.async_on_unload(async_dispatcher_connect(hass, f"{SIGNAAL_BIJGEWERKT}_{entry.entry_id}", nieuwe))


class EnergieplanPauze(ZonneburenEntiteit, ButtonEntity):
    _attr_icon = "mdi:pause-circle-outline"

    def __init__(self, hub) -> None:
        super().__init__(hub, "energieplan_pauze")

    async def async_press(self) -> None:
        self.hub.ems.pauzeer(PAUZE_S)


class EnergieplanHervat(ZonneburenEntiteit, ButtonEntity):
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, hub) -> None:
        super().__init__(hub, "energieplan_hervat")

    async def async_press(self) -> None:
        self.hub.ems.pauzeer(0)


class StartSlim(ZonneburenEntiteit, ButtonEntity):
    """Programma klaargezet en op start gedrukt: de stekker gaat uit tot het beste moment voor de eindtijd."""

    _attr_icon = "mdi:washing-machine"

    def __init__(self, hub, index: int) -> None:
        super().__init__(hub, f"start_slim_{index}")
        self.index = index
        self._attr_translation_key = None
        self._attr_name = f"{naam(hub, index)} slim starten"

    async def async_press(self) -> None:
        klaar = self.hub.sturing.klaar_om.get(self.index)
        if klaar is None or klaar <= time.time():
            klaar = time.time() + STANDAARD_UREN * 3600
        if fout := await self.hub.klaarzetten(self.index, klaar):
            raise HomeAssistantError(fout)


class StopSlim(ZonneburenEntiteit, ButtonEntity):
    _attr_icon = "mdi:close-circle-outline"

    def __init__(self, hub, index: int) -> None:
        super().__init__(hub, f"stop_slim_{index}")
        self.index = index
        self._attr_translation_key = None
        self._attr_name = f"{naam(hub, index)} nu starten"

    async def async_press(self) -> None:
        if fout := await self.hub.klaarzetten(self.index, None):
            raise HomeAssistantError(fout)
