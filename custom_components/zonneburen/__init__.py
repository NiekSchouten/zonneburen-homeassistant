"""Zonneburen: Home Assistant als hub van je huis in de energiewijk."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

import logging

from . import paneel
from .const import CONF_EMS_PROFIEL, CONF_EMS_VERMOGEN, DOMAIN, PROFIEL_VERMOGEN
from .hub import ZonneburenHub

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]

type ZonneburenConfigEntry = ConfigEntry[ZonneburenHub]


_LOGGER = logging.getLogger(__name__)

# Getallen die een batterij nooit sturen; zo'n keuze uit een oudere versie werkte niet en halen we weg.
_GEEN_BATTERIJVERMOGEN = ("zero_export", "export_limit", "peak_shaving", "max_export", "max_import", "pv_power", "generator", "smartload")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 naar 1.2: het sturingsprofiel. Wie een vermogen had, houdt dat; een teruglevergrens vervalt."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        opties = dict(entry.options)
        vermogen = opties.get(CONF_EMS_VERMOGEN) or ""
        if any(woord in vermogen for woord in _GEEN_BATTERIJVERMOGEN):
            _LOGGER.warning("Zonneburen: %s stuurt geen batterij en is weggehaald. Kies bij Configureren > Thuisbatterij opnieuw.", vermogen)
            opties[CONF_EMS_VERMOGEN] = None
        elif vermogen and not opties.get(CONF_EMS_PROFIEL):
            opties[CONF_EMS_PROFIEL] = PROFIEL_VERMOGEN
        hass.config_entries.async_update_entry(entry, options=opties, minor_version=2)
    return True


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
