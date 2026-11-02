"""Verbinding, wijkstroom en noodstop als aan/uit."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZonneburenConfigEntry
from .entiteit import ZonneburenEntiteit


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    voeg_toe([Verbonden(hub), WijkstroomActief(hub), Noodstop(hub)])


class Verbonden(ZonneburenEntiteit, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub) -> None:
        super().__init__(hub, "verbonden")

    @property
    def is_on(self) -> bool:
        return self.hub.verbinding.verbonden


class WijkstroomActief(ZonneburenEntiteit, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, hub) -> None:
        super().__init__(hub, "wijkstroom")

    @property
    def is_on(self) -> bool:
        return self.hub.wijk_actief


class Noodstop(ZonneburenEntiteit, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, hub) -> None:
        super().__init__(hub, "noodstop")

    @property
    def is_on(self) -> bool:
        return self.hub.noodstop
