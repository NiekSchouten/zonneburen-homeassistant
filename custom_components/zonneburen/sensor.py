"""Wat de wijk meldt: overschot, beursprijs en het weekrapport."""

from __future__ import annotations

import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZonneburenConfigEntry, protocol
from .entiteit import ZonneburenEntiteit


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    voeg_toe([WijkOverschot(hub), Beursprijs(hub), GedeeldDezeWeek(hub)])


class WijkOverschot(ZonneburenEntiteit, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, hub) -> None:
        super().__init__(hub, "wijk_overschot")

    @property
    def native_value(self) -> float | None:
        return self.hub.wijk_overschot


class Beursprijs(ZonneburenEntiteit, SensorEntity):
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_suggested_display_precision = 3

    def __init__(self, hub) -> None:
        super().__init__(hub, "beursprijs")

    @property
    def native_value(self) -> float | None:
        return protocol.prijs_nu(self.hub.prijzen, time.time())


class GedeeldDezeWeek(ZonneburenEntiteit, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, hub) -> None:
        super().__init__(hub, "gedeeld_week")

    @property
    def native_value(self) -> float | None:
        return self.hub.week["gedeeld_kwh"] if self.hub.week else None

    @property
    def extra_state_attributes(self) -> dict | None:
        return {"plek": self.hub.week["plek"], "huizen": self.hub.week["huizen"]} if self.hub.week else None
