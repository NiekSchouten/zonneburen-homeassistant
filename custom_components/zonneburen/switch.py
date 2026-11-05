"""Het energieplan uitvoeren aan of uit, vanuit Home Assistant zelf."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZonneburenConfigEntry
from .const import CONF_EMS_AAN
from .entiteit import ZonneburenEntiteit


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    voeg_toe([EnergieplanUitvoeren(entry.runtime_data)])


class EnergieplanUitvoeren(ZonneburenEntiteit, SwitchEntity):
    _attr_icon = "mdi:battery-sync"

    def __init__(self, hub) -> None:
        super().__init__(hub, "energieplan_uitvoeren")

    @property
    def is_on(self) -> bool:
        return bool(self.hub.opties.get(CONF_EMS_AAN))

    async def async_turn_on(self, **_kwargs) -> None:
        self._zet(True)

    async def async_turn_off(self, **_kwargs) -> None:
        self._zet(False)

    def _zet(self, aan: bool) -> None:
        self.hass.config_entries.async_update_entry(self.hub.entry, options={**self.hub.opties, CONF_EMS_AAN: aan})
        self.hub._log(0, "Energieplan uitvoeren " + ("aangezet" if aan else "uitgezet") + " in Home Assistant")
        self.hub._bijgewerkt()
