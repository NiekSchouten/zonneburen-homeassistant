"""Snelknoppen voor het energieplan: twee uur pauze, of weer volgens plan."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZonneburenConfigEntry
from .entiteit import ZonneburenEntiteit

PAUZE_S = 2 * 3600


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    voeg_toe([EnergieplanPauze(hub), EnergieplanHervat(hub)])


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
