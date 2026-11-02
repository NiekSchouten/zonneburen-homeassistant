"""Koppelen met een code uit de webapp, daarna de sensoren kiezen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import instance_id, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import protocol
from .const import (
    CONF_ADRES, CONF_APPARAAT_ID, CONF_BATTERIJ, CONF_BATTERIJ_OM, CONF_BEDIENING, CONF_MQTT_GEBRUIKER, CONF_MQTT_HOST,
    CONF_MQTT_POORT, CONF_MQTT_WACHTWOORD, CONF_NET, CONF_NET_OM, CONF_SERVER, CONF_SOC, CONF_VERBRUIK,
    CONF_WIJK_AUTOMATISCH, CONF_ZON, DOMAIN, SENSOREN, STANDAARD_SERVER,
)
from .koppelen import KoppelFout, koppel

CONF_CODE = "code"


def _vermogen() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="power"))


def _schema(standaard: dict) -> vol.Schema:
    """Alleen het net is verplicht: daarmee doet een huis mee aan de verrekening."""
    velden: dict = {vol.Required(CONF_NET, description={"suggested_value": standaard.get(CONF_NET)}): _vermogen(),
                    vol.Optional(CONF_NET_OM, default=standaard.get(CONF_NET_OM, False)): bool}
    for sleutel, kiezer in ((CONF_ZON, _vermogen()), (CONF_BATTERIJ, _vermogen()),
                            (CONF_SOC, selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="battery"))),
                            (CONF_VERBRUIK, _vermogen())):
        velden[vol.Optional(sleutel, description={"suggested_value": standaard.get(sleutel)})] = kiezer
    velden[vol.Optional(CONF_BATTERIJ_OM, default=standaard.get(CONF_BATTERIJ_OM, False))] = bool
    velden[vol.Optional(CONF_WIJK_AUTOMATISCH, default=standaard.get(CONF_WIJK_AUTOMATISCH, True))] = bool
    velden[vol.Optional(CONF_BEDIENING, default=standaard.get(CONF_BEDIENING, True))] = bool
    return vol.Schema(velden)


def _keuzes(invoer: dict) -> dict:
    return {sleutel: invoer.get(sleutel) for sleutel in (*SENSOREN, CONF_NET_OM, CONF_BATTERIJ_OM, CONF_WIJK_AUTOMATISCH, CONF_BEDIENING)}


class ZonneburenConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._gekoppeld: dict = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fouten: dict[str, str] = {}
        if user_input is not None:
            apparaat = protocol.apparaat_id(await instance_id.async_get(self.hass))
            try:
                gegevens = await koppel(async_get_clientsession(self.hass), user_input[CONF_SERVER], user_input[CONF_CODE], apparaat)
            except KoppelFout as fout:
                fouten["base"] = fout.reden
            else:
                await self.async_set_unique_id(gegevens["adres"].strip().lower())
                self._abort_if_unique_id_configured()
                self._gekoppeld = {
                    CONF_SERVER: user_input[CONF_SERVER].rstrip("/"), CONF_ADRES: gegevens["adres"], CONF_APPARAAT_ID: apparaat,
                    CONF_MQTT_HOST: gegevens["host"], CONF_MQTT_POORT: int(gegevens["poort"]),
                    CONF_MQTT_GEBRUIKER: gegevens["gebruiker"], CONF_MQTT_WACHTWOORD: gegevens["wachtwoord"],
                }
                return await self.async_step_sensoren()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_SERVER, default=STANDAARD_SERVER): str,
                vol.Required(CONF_CODE): str,
            }),
            errors=fouten,
        )

    async def async_step_sensoren(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=self._gekoppeld[CONF_ADRES], data=self._gekoppeld, options=_keuzes(user_input))

        return self.async_show_form(step_id="sensoren", data_schema=_schema({}),
                                    description_placeholders={"adres": self._gekoppeld[CONF_ADRES]})

    @staticmethod
    @callback
    def async_get_options_flow(_entry: ConfigEntry) -> OptionsFlow:
        return ZonneburenOptionsFlow()


class ZonneburenOptionsFlow(OptionsFlow):
    """De sensoren later wijzigen. Apparaten en wat de webapp instelt, blijven staan."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **_keuzes(user_input)})
        return self.async_show_form(step_id="init", data_schema=_schema(dict(self.config_entry.options)))
