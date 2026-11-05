"""Koppelen met een code uit de webapp, daarna de sensoren kiezen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import instance_id, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import protocol
from .const import (
    CONF_ADRES, CONF_APPARAAT_ID, CONF_BATTERIJ, CONF_BATTERIJ_OM, CONF_BEDIENING, CONF_MQTT_GEBRUIKER, CONF_MQTT_HOST,
    CONF_MQTT_POORT, CONF_MQTT_WACHTWOORD, CONF_NET, CONF_NET_OM, CONF_SERVER, CONF_SOC, CONF_VERBRUIK,
    CONF_WIJK_AUTOMATISCH, CONF_ZON, DOMAIN, SENSOREN, STANDAARD_SERVER,
    CONF_EMS_AAN, CONF_EMS_LADEN_POSITIEF, CONF_EMS_MAX_LAAD_W, CONF_EMS_MAX_ONTLAAD_W, CONF_EMS_MODUS, CONF_EMS_MODUS_STUREN,
    CONF_EMS_MODUS_TERUG, CONF_EMS_NETDOEL, CONF_EMS_VERMOGEN, CONF_EMS_ZEKERING_A, EMS_OPTIES,
)
from .koppelen import KoppelFout, koppel

CONF_CODE = "code"
SECTIE_EMS = "energieplan"


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


def _ems_schema(standaard: dict) -> vol.Schema:
    """Het energieplan: de entiteit die de batterij stuurt en de lokale grenzen. Alles optioneel."""
    def voorstel(sleutel):
        return {"suggested_value": standaard.get(sleutel)}
    return vol.Schema({
        vol.Optional(CONF_EMS_AAN, default=standaard.get(CONF_EMS_AAN, False)): bool,
        vol.Optional(CONF_EMS_VERMOGEN, description=voorstel(CONF_EMS_VERMOGEN)):
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["number", "input_number"])),
        vol.Optional(CONF_EMS_NETDOEL, default=standaard.get(CONF_EMS_NETDOEL, False)): bool,
        vol.Optional(CONF_EMS_LADEN_POSITIEF, default=standaard.get(CONF_EMS_LADEN_POSITIEF, False)): bool,
        vol.Optional(CONF_EMS_MODUS, description=voorstel(CONF_EMS_MODUS)):
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["select", "input_select"])),
        vol.Optional(CONF_EMS_MODUS_STUREN, description=voorstel(CONF_EMS_MODUS_STUREN)): str,
        vol.Optional(CONF_EMS_MODUS_TERUG, description=voorstel(CONF_EMS_MODUS_TERUG)): str,
        vol.Optional(CONF_EMS_MAX_LAAD_W, description=voorstel(CONF_EMS_MAX_LAAD_W)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50000, step=100, unit_of_measurement="W", mode="box")),
        vol.Optional(CONF_EMS_MAX_ONTLAAD_W, description=voorstel(CONF_EMS_MAX_ONTLAAD_W)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50000, step=100, unit_of_measurement="W", mode="box")),
        vol.Optional(CONF_EMS_ZEKERING_A, description=voorstel(CONF_EMS_ZEKERING_A)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=80, step=1, unit_of_measurement="A", mode="box")),
    })


def _keuzes(invoer: dict) -> dict:
    keuzes = {sleutel: invoer.get(sleutel) for sleutel in (*SENSOREN, CONF_NET_OM, CONF_BATTERIJ_OM, CONF_WIJK_AUTOMATISCH, CONF_BEDIENING)}
    if isinstance(ems := invoer.get(SECTIE_EMS), dict):
        keuzes.update({sleutel: ems.get(sleutel) for sleutel in EMS_OPTIES})
    return keuzes


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
        opties = dict(self.config_entry.options)
        schema = _schema(opties).extend({
            vol.Required(SECTIE_EMS): section(_ems_schema(opties), {"collapsed": not opties.get(CONF_EMS_AAN)}),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
