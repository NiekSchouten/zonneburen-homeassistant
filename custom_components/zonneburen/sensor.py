"""Wat de wijk meldt: overschot, beursprijs en het weekrapport."""

from __future__ import annotations

import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZonneburenConfigEntry, ems_core, protocol
from .entiteit import ZonneburenEntiteit


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    voeg_toe([WijkOverschot(hub), Beursprijs(hub), GedeeldDezeWeek(hub), Energieplan(hub), VerwachteBesparing(hub)])


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


class Energieplan(ZonneburenEntiteit, SensorEntity):
    """Wat de batterij nu volgens het plan doet, met de komende blokken als attributen (voor de kaart)."""

    _attr_icon = "mdi:battery-clock"

    def __init__(self, hub) -> None:
        super().__init__(hub, "energieplan")

    @property
    def native_value(self) -> str | None:
        laatste = self.hub.ems.laatste
        if not self.hub.ems.plan and not laatste:
            return None
        return ems_core.ZINNEN[laatste.stand] if laatste else None

    @property
    def extra_state_attributes(self) -> dict:
        ems = self.hub.ems
        attributen = ems.info()
        if ems.laatste and ems.laatste.afwijking:
            attributen["afwijking"] = ems.laatste.afwijking
        if ems.plan:
            nu = time.time()
            attributen.update(
                plan_id=ems.plan.plan_id, geldig_tot=ems.plan.geldig_tot,
                blokken=ems_core.blokken(ems.plan, nu),
                # Compact voor de tijdlijn op de kaart: [begin, stand, lading aan het eind, inkoopprijs]
                kwartieren=[[ems.plan.t0 + i * ems.plan.stap, k.stand, k.soc_eind, k.prijs_in]
                            for i, k in enumerate(ems.plan.kwartieren) if ems.plan.t0 + (i + 1) * ems.plan.stap > nu][:144],
            )
        return attributen


class VerwachteBesparing(ZonneburenEntiteit, SensorEntity):
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, hub) -> None:
        super().__init__(hub, "verwachte_besparing")

    @property
    def native_value(self) -> float | None:
        plan = self.hub.ems.plan
        return plan.verwacht.get("besparingBatterij") if plan else None

    @property
    def extra_state_attributes(self) -> dict | None:
        plan = self.hub.ems.plan
        return {"door_slim_sturen": plan.verwacht.get("besparingSturen")} if plan and plan.verwacht else None
