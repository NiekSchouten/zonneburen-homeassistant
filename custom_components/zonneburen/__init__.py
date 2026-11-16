"""Zonneburen: Home Assistant als hub van je huis in de energiewijk."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import paneel
from .const import DOMAIN
from .hub import ZonneburenHub

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]

type ZonneburenConfigEntry = ConfigEntry[ZonneburenHub]


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry) -> bool:
    hub = ZonneburenHub(hass, entry)
    await hub.async_start()
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await paneel.registreer(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry) -> bool:
    if gelukt := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
        andere = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED]
        if not andere:
            paneel.verwijder(hass)
    return gelukt
