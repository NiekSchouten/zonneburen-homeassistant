"""Diagnose om mee te sturen bij een probleem, zonder het wachtwoord van de broker."""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ZonneburenConfigEntry
from .const import CONF_MQTT_WACHTWOORD


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ZonneburenConfigEntry) -> dict:
    hub = entry.runtime_data
    return {
        "data": async_redact_data(dict(entry.data), {CONF_MQTT_WACHTWOORD}),
        "opties": dict(entry.options),
        "verbonden": hub.verbinding.verbonden,
        "live": hub.live,
        "wijk_actief": hub.wijk_actief,
        "noodstop": hub.noodstop,
        "standen": hub.standen,
        "logboek": list(hub.logboek)[:50],
    }
