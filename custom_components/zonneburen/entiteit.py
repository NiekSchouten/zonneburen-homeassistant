"""Gedeelde basis voor de entiteiten: één apparaat per huis, ververst als de hub iets nieuws heeft."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAAL_BIJGEWERKT, VERSIE
from .hub import ZonneburenHub


class ZonneburenEntiteit(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: ZonneburenHub, sleutel: str) -> None:
        self.hub = hub
        self._attr_translation_key = sleutel
        self._attr_unique_id = f"{hub.hub_id}_{sleutel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.hub_id)}, name=f"Zonneburen {hub.adres}", manufacturer="Zonneburen",
            model="Home Assistant als hub", sw_version=VERSIE, configuration_url=hub.entry.data.get("server"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{SIGNAAL_BIJGEWERKT}_{self.hub.entry.entry_id}", self.async_write_ha_state))
