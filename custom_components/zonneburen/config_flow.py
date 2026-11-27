"""Koppelen met een code uit de webapp; daarna vult de integratie zelf zoveel mogelijk in.

Stappen bij het koppelen: code → sensoren → apparaten → thuisbatterij → standen. Elke stap na de code
komt met een voorstel uit herkennen.py; stappen zonder iets om voor te stellen worden overgeslagen.
Dezelfde stappen staan later in het menu onder Configureren.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr, entity_registry as er, instance_id, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import herkennen, protocol
from .const import (
    CONF_ADRES, CONF_APPARAAT_ID, CONF_APPARATEN, CONF_BATTERIJ, CONF_BATTERIJ_OM, CONF_BEDIENING, CONF_EMS_AAN,
    CONF_EMS_LADEN_POSITIEF, CONF_EMS_MAX_LAAD_W, CONF_EMS_MAX_ONTLAAD_W, CONF_EMS_MODUS, CONF_EMS_MODUS_STUREN,
    CONF_EMS_MODUS_TERUG, CONF_EMS_NETDOEL, CONF_EMS_ONTLAAD, CONF_EMS_RICHTING, CONF_EMS_RICHTING_LADEN,
    CONF_EMS_RICHTING_ONTLADEN, CONF_EMS_RICHTING_STOP, CONF_EMS_VERMOGEN, CONF_EMS_ZEKERING_A, CONF_MQTT_GEBRUIKER,
    CONF_MQTT_HOST, CONF_MQTT_POORT, CONF_MQTT_WACHTWOORD, CONF_NET, CONF_NET_OM, CONF_NET_TERUG, CONF_SERVER, CONF_SOC,
    CONF_VERBRUIK, CONF_WIJK_AUTOMATISCH, CONF_ZON, DOMAIN, SENSOREN, STANDAARD_SERVER,
    CONF_EMS_PROFIEL, CONF_TOU, CONF_TOU_LADEN, CONF_TOU_MAX_PCT, CONF_TOU_MIN_PCT, CONF_TOU_MIN_SOC, CONF_TOU_SOC,
    CONF_TOU_SPANNING, CONF_TOU_STROOM, CONF_TOU_TIJD, PROFIEL_DEYE, PROFIEL_VERMOGEN,
    CONF_TOU_FASEN, CONF_TOU_MAX_EXPORT, CONF_TOU_MAX_LAAD_A, CONF_TOU_MAX_LAAD_W, CONF_TOU_MAX_ONTLAAD_A,
    CONF_TOU_MAX_ONTLAAD_W, CONF_TOU_NETLADEN, CONF_TOU_PATROON, CONF_TOU_TERUGLEVEREN, CONF_TOU_VERMOGEN, CONF_TOU_WERKMODUS,
)
from .koppelen import KoppelFout, koppel

_LOGGER = logging.getLogger(__name__)

CONF_CODE = "code"
CONF_KIES = "kies"
SECTIE_DETAILS = "details"
DOMEINEN = ("sensor", "number", "input_number", "select", "input_select", "switch", "climate", "water_heater", "time")


# ---------------------------------------------------------------------------------------------- Wat Home Assistant heeft

def _echt_apparaat(register, apparaat):
    """Het apparaat zelf, of bij een subapparaat zijn ouder. Een ChildDeviceEntry heeft geen fabrikant of model:
    daar vragen we niet naar, anders waarschuwt Home Assistant."""
    for _ in range(3):
        if apparaat is None or type(apparaat) is dr.DeviceEntry:
            return apparaat
        ouder = getattr(apparaat, "via_device_id", None) or getattr(apparaat, "parent_id", None)
        apparaat = register.async_get(ouder) if ouder else None
    return None


async def inventaris(hass: HomeAssistant) -> tuple[list[herkennen.Entiteit], dict]:
    """Alle entiteiten waar iets in kan zitten, met hun integratie en apparaat, en de voorkeuren van het Energie-dashboard."""
    entiteiten_reg, apparaten_reg = er.async_get(hass), dr.async_get(hass)
    lijst = []
    for staat in hass.states.async_all(DOMEINEN):
        invoer = entiteiten_reg.async_get(staat.entity_id)
        apparaat = apparaten_reg.async_get(invoer.device_id) if invoer and invoer.device_id else None
        echt = _echt_apparaat(apparaten_reg, apparaat)
        attrs = staat.attributes
        lijst.append(herkennen.Entiteit(
            entity_id=staat.entity_id,
            platform=invoer.platform if invoer else "",
            apparaat=invoer.device_id if invoer else None,
            apparaatnaam=(apparaat.name_by_user or apparaat.name or "") if apparaat else "",
            merk=(echt.manufacturer or "") if echt else "",
            model=(echt.model or "") if echt else "",
            naam=str(attrs.get("friendly_name") or ""),
            sleutel=" ".join(str(d) for d in (invoer.translation_key, invoer.unique_id, invoer.original_name) if d) if invoer else "",
            device_class=attrs.get("device_class"),
            eenheid=attrs.get("unit_of_measurement"),
            staat=staat.state,
            opties=[str(o) for o in attrs.get("options") or []],
            minimum=_getal(attrs.get("min")),
            maximum=_getal(attrs.get("max")),
            hvac_modi=[str(m) for m in attrs.get("hvac_modes") or []],
            doeltemp=_getal(attrs.get("temperature")),
            max_temp=_getal(attrs.get("max_temp")),
        ))
    voorkeuren: dict = {}
    try:
        from homeassistant.components.energy.data import async_get_manager

        voorkeuren = (await async_get_manager(hass)).data or {}
    except Exception:  # noqa: BLE001 - zonder Energie-dashboard zoeken we gewoon verder
        _LOGGER.debug("Geen voorkeuren van het Energie-dashboard", exc_info=True)
    return lijst, dict(voorkeuren)


def _getal(waarde) -> float | None:
    try:
        return float(waarde)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------- Formulieren

def _vermogen() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="power"))


def _voorstel(waarde) -> dict:
    return {"suggested_value": waarde}


def _sensoren_schema(standaard: dict) -> vol.Schema:
    """Alleen het net is verplicht: daarmee doet een huis mee aan de verrekening."""
    return vol.Schema({
        vol.Required(CONF_NET, description=_voorstel(standaard.get(CONF_NET))): _vermogen(),
        vol.Optional(CONF_NET_TERUG, description=_voorstel(standaard.get(CONF_NET_TERUG))): _vermogen(),
        vol.Optional(CONF_NET_OM, default=bool(standaard.get(CONF_NET_OM))): bool,
        vol.Optional(CONF_ZON, description=_voorstel(standaard.get(CONF_ZON))): _vermogen(),
        vol.Optional(CONF_BATTERIJ, description=_voorstel(standaard.get(CONF_BATTERIJ))): _vermogen(),
        vol.Optional(CONF_BATTERIJ_OM, default=bool(standaard.get(CONF_BATTERIJ_OM))): bool,
        vol.Optional(CONF_SOC, description=_voorstel(standaard.get(CONF_SOC))):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_VERBRUIK, description=_voorstel(standaard.get(CONF_VERBRUIK))): _vermogen(),
        vol.Optional(CONF_WIJK_AUTOMATISCH, default=standaard.get(CONF_WIJK_AUTOMATISCH, True)): bool,
        vol.Optional(CONF_BEDIENING, default=standaard.get(CONF_BEDIENING, True)): bool,
    })


def deye_opties(deye: herkennen.DeyeTou) -> dict:
    """De gevonden entiteiten van een Deye als opties van het profiel."""
    return {CONF_TOU: deye.tou, CONF_TOU_LADEN: deye.laden, CONF_TOU_SOC: deye.soc, CONF_TOU_TIJD: deye.tijd,
            CONF_TOU_STROOM: deye.stroom, CONF_TOU_SPANNING: deye.spanning, CONF_TOU_MIN_SOC: deye.min_soc,
            CONF_TOU_WERKMODUS: deye.werkmodus, CONF_TOU_PATROON: deye.patroon, CONF_TOU_VERMOGEN: deye.vermogen,
            CONF_TOU_MAX_LAAD_A: deye.max_laad_a, CONF_TOU_MAX_ONTLAAD_A: deye.max_ontlaad_a, CONF_TOU_NETLADEN: deye.netladen,
            CONF_TOU_MAX_EXPORT: deye.max_export}


def _deye_schema(standaard: dict) -> vol.Schema:
    meer = lambda domein: selector.EntitySelector(selector.EntitySelectorConfig(domain=domein, multiple=True))
    een = lambda domein: selector.EntitySelector(selector.EntitySelectorConfig(domain=domein))
    procent = lambda lo, hi: selector.NumberSelector(selector.NumberSelectorConfig(min=lo, max=hi, step=1, unit_of_measurement="%", mode="box"))
    watt = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50000, step=100, unit_of_measurement="W", mode="box"))
    return vol.Schema({
        vol.Optional(CONF_TOU, description=_voorstel(standaard.get(CONF_TOU))): een("select"),
        vol.Optional(CONF_TOU_LADEN, description=_voorstel(standaard.get(CONF_TOU_LADEN))): meer("select"),
        vol.Optional(CONF_TOU_SOC, description=_voorstel(standaard.get(CONF_TOU_SOC))): meer("number"),
        vol.Optional(CONF_TOU_TIJD, description=_voorstel(standaard.get(CONF_TOU_TIJD))): meer("time"),
        vol.Optional(CONF_TOU_STROOM, description=_voorstel(standaard.get(CONF_TOU_STROOM))): een("number"),
        vol.Optional(CONF_TOU_SPANNING, description=_voorstel(standaard.get(CONF_TOU_SPANNING))): een("sensor"),
        vol.Optional(CONF_TOU_MIN_SOC, description=_voorstel(standaard.get(CONF_TOU_MIN_SOC))): een("number"),
        vol.Optional(CONF_TOU_WERKMODUS, description=_voorstel(standaard.get(CONF_TOU_WERKMODUS))): een("select"),
        vol.Optional(CONF_TOU_PATROON, description=_voorstel(standaard.get(CONF_TOU_PATROON))): een("select"),
        vol.Optional(CONF_TOU_VERMOGEN, description=_voorstel(standaard.get(CONF_TOU_VERMOGEN))): meer("number"),
        vol.Optional(CONF_TOU_MAX_LAAD_A, description=_voorstel(standaard.get(CONF_TOU_MAX_LAAD_A))): een("number"),
        vol.Optional(CONF_TOU_MAX_ONTLAAD_A, description=_voorstel(standaard.get(CONF_TOU_MAX_ONTLAAD_A))): een("number"),
        vol.Optional(CONF_TOU_NETLADEN, description=_voorstel(standaard.get(CONF_TOU_NETLADEN))): een("switch"),
        vol.Optional(CONF_TOU_MAX_EXPORT, description=_voorstel(standaard.get(CONF_TOU_MAX_EXPORT))): een("number"),
        vol.Optional(CONF_TOU_MAX_PCT, default=standaard.get(CONF_TOU_MAX_PCT) or 90): procent(30, 100),
        vol.Optional(CONF_TOU_MIN_PCT, default=standaard.get(CONF_TOU_MIN_PCT) or 20): procent(5, 80),
        vol.Optional(CONF_TOU_MAX_LAAD_W, default=standaard.get(CONF_TOU_MAX_LAAD_W) or 10000): watt,
        vol.Optional(CONF_TOU_MAX_ONTLAAD_W, default=standaard.get(CONF_TOU_MAX_ONTLAAD_W) or 10000): watt,
        vol.Optional(CONF_TOU_TERUGLEVEREN, default=bool(standaard.get(CONF_TOU_TERUGLEVEREN))): bool,
        vol.Optional(CONF_TOU_FASEN, description=_voorstel(standaard.get(CONF_TOU_FASEN))): meer("sensor"),
        vol.Optional(CONF_EMS_ZEKERING_A, description=_voorstel(standaard.get(CONF_EMS_ZEKERING_A))):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=80, step=1, unit_of_measurement="A", mode="box")),
    })


def _batterij_schema(standaard: dict) -> vol.Schema:
    getal = selector.EntitySelector(selector.EntitySelectorConfig(domain=["number", "input_number"]))
    watt = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50000, step=100, unit_of_measurement="W", mode="box"))
    details = vol.Schema({
        vol.Optional(CONF_EMS_VERMOGEN, description=_voorstel(standaard.get(CONF_EMS_VERMOGEN))): getal,
        vol.Optional(CONF_EMS_ONTLAAD, description=_voorstel(standaard.get(CONF_EMS_ONTLAAD))): getal,
        vol.Optional(CONF_EMS_NETDOEL, default=bool(standaard.get(CONF_EMS_NETDOEL))): bool,
        vol.Optional(CONF_EMS_LADEN_POSITIEF, default=bool(standaard.get(CONF_EMS_LADEN_POSITIEF))): bool,
        vol.Optional(CONF_EMS_MODUS, description=_voorstel(standaard.get(CONF_EMS_MODUS))):
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["select", "input_select", "switch", "input_boolean"])),
        vol.Optional(CONF_EMS_RICHTING, description=_voorstel(standaard.get(CONF_EMS_RICHTING))):
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["select", "input_select"])),
        vol.Optional(CONF_EMS_MAX_LAAD_W, description=_voorstel(standaard.get(CONF_EMS_MAX_LAAD_W))): watt,
        vol.Optional(CONF_EMS_MAX_ONTLAAD_W, description=_voorstel(standaard.get(CONF_EMS_MAX_ONTLAAD_W))): watt,
        vol.Optional(CONF_EMS_ZEKERING_A, description=_voorstel(standaard.get(CONF_EMS_ZEKERING_A))):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=80, step=1, unit_of_measurement="A", mode="box")),
    })
    return vol.Schema({
        vol.Optional(CONF_EMS_AAN, default=bool(standaard.get(CONF_EMS_AAN))): bool,
        vol.Optional(CONF_EMS_PROFIEL, default=standaard.get(CONF_EMS_PROFIEL) or PROFIEL_VERMOGEN): selector.SelectSelector(
            selector.SelectSelectorConfig(options=[PROFIEL_VERMOGEN, PROFIEL_DEYE], translation_key="profiel",
                                          mode=selector.SelectSelectorMode.LIST)),
        # Ingevuld door de integratie; openklappen hoeft alleen als iets niet klopt.
        vol.Required(SECTIE_DETAILS): section(details, {"collapsed": bool(standaard.get(CONF_EMS_VERMOGEN))}),
    })


def _keuze(opties: list[str], standaard: str | None) -> tuple[dict, Any]:
    if opties:
        return ({"default": standaard} if standaard in opties else {},
                selector.SelectSelector(selector.SelectSelectorConfig(options=opties, mode=selector.SelectSelectorMode.DROPDOWN)))
    return {"description": _voorstel(standaard)}, str


# ---------------------------------------------------------------------------------------------- Stappen

class _Stappen:
    """De stappen na het koppelen, gedeeld door het koppelen en het menu Configureren."""

    hass: HomeAssistant
    _opties: dict
    _entiteiten: list[herkennen.Entiteit] | None = None
    _sensoren: herkennen.Sensoren | None = None

    async def _herken(self) -> None:
        if self._entiteiten is None:
            self._entiteiten, voorkeuren = await inventaris(self.hass)
            self._sensoren = herkennen.zoek_sensoren(self._entiteiten, voorkeuren)

    async def _volgende(self, na: str) -> ConfigFlowResult:
        raise NotImplementedError

    # Sensoren ---------------------------------------------------------------------------------------------

    async def async_step_sensoren(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self._herken()
        if user_input is not None:
            for sleutel in (*SENSOREN, CONF_NET_OM, CONF_BATTERIJ_OM, CONF_WIJK_AUTOMATISCH, CONF_BEDIENING):
                self._opties[sleutel] = user_input.get(sleutel)
            return await self._volgende("sensoren")

        standaard = dict(self._opties)
        gevonden = self._sensoren
        if gevonden and not standaard.get(CONF_NET):
            # Alleen bij een leeg formulier: wat de bewoner al koos, blijft staan.
            standaard.update({CONF_NET: gevonden.net, CONF_NET_TERUG: gevonden.net_terug, CONF_NET_OM: gevonden.net_om,
                              CONF_ZON: gevonden.zon, CONF_BATTERIJ: gevonden.batterij, CONF_BATTERIJ_OM: gevonden.batterij_om,
                              CONF_SOC: gevonden.soc, CONF_VERBRUIK: gevonden.verbruik})
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="sensoren", data_schema=_sensoren_schema(standaard),
            description_placeholders={"adres": getattr(self, "_gekoppeld", {}).get(CONF_ADRES, ""),
                                      "gevonden": self._overzicht(standaard)})

    def _overzicht(self, standaard: dict) -> str:
        """Wat er nu gemeten wordt, zodat de bewoner ziet of het teken klopt."""
        regels = []
        for sleutel, naam in ((CONF_NET, "Net"), (CONF_ZON, "Zon"), (CONF_BATTERIJ, "Batterij"), (CONF_SOC, "Laadniveau"),
                              (CONF_VERBRUIK, "Verbruik")):
            entity_id = standaard.get(sleutel)
            if not entity_id or not (staat := self.hass.states.get(entity_id)):
                continue
            waarde = _getal(staat.state)
            if sleutel == CONF_SOC:
                tekst = f"{waarde:.0f} %" if waarde is not None else "geen waarde"
            else:
                watt = self._watt(entity_id)
                if sleutel == CONF_NET and standaard.get(CONF_NET_TERUG):
                    terug = self._watt(standaard[CONF_NET_TERUG])
                    watt = None if watt is None or terug is None else watt - terug
                if watt is not None and standaard.get({CONF_NET: CONF_NET_OM, CONF_BATTERIJ: CONF_BATTERIJ_OM}.get(sleutel, "")):
                    watt = -watt
                tekst = "geen waarde" if watt is None else f"{abs(watt):.0f} W" + {
                    CONF_NET: " afname" if watt > 0 else " teruglevering" if watt < 0 else "",
                    CONF_BATTERIJ: " ontladen" if watt > 0 else " laden" if watt < 0 else "",
                }.get(sleutel, "")
            regels.append(f"- **{naam}:** {staat.name}: nu {tekst}")
        return "\n".join(regels) if regels else "- Nog niets gevonden: kies hieronder je sensoren."

    def _watt(self, entity_id: str) -> float | None:
        staat = self.hass.states.get(entity_id)
        waarde = _getal(staat.state) if staat else None
        if waarde is not None and staat and staat.attributes.get("unit_of_measurement") == "kW":
            waarde *= 1000
        return waarde

    # Apparaten --------------------------------------------------------------------------------------------

    def _voorstellen(self) -> list[herkennen.ApparaatVoorstel]:
        gekozen = {a["host"] for a in protocol.normaliseer_apparaten(self._opties.get(CONF_APPARATEN)) if a["host"]}
        return herkennen.zoek_apparaten(self._entiteiten or [], gekozen)

    async def async_step_apparaten(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self._herken()
        voorstellen = self._voorstellen()
        if user_input is not None:
            kies = set(user_input.get(CONF_KIES) or [])
            apparaten = protocol.normaliseer_apparaten(self._opties.get(CONF_APPARATEN))
            vrij = [i for i, a in enumerate(apparaten) if not a["enabled"] and not a["host"]]
            for voorstel in (v for v in voorstellen if v.entity_id in kies):
                if vrij:
                    apparaten[vrij.pop(0)] = voorstel.als_apparaat()
            self._opties[CONF_APPARATEN] = apparaten
            return await self._volgende("apparaten")
        if not voorstellen:
            return await self._volgende("apparaten")

        opties = [selector.SelectOptionDict(value=v.entity_id, label=f"{v.soort}: {v.naam}" + (" (met vermogensmeting)" if v.vermogen else ""))
                  for v in voorstellen]
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="apparaten",
            data_schema=vol.Schema({vol.Optional(CONF_KIES, default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=opties, multiple=True, mode=selector.SelectSelectorMode.LIST))}))

    # Thuisbatterij ----------------------------------------------------------------------------------------

    async def async_step_batterij(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self._herken()
        if user_input is not None:
            details = user_input.get(SECTIE_DETAILS) or {}
            self._opties[CONF_EMS_AAN] = bool(user_input.get(CONF_EMS_AAN))
            self._opties[CONF_EMS_PROFIEL] = user_input.get(CONF_EMS_PROFIEL) or PROFIEL_VERMOGEN
            if self._opties[CONF_EMS_PROFIEL] == PROFIEL_DEYE:
                return await self.async_step_deye()
            for sleutel in (CONF_EMS_VERMOGEN, CONF_EMS_ONTLAAD, CONF_EMS_NETDOEL, CONF_EMS_LADEN_POSITIEF, CONF_EMS_MODUS,
                            CONF_EMS_RICHTING, CONF_EMS_MAX_LAAD_W, CONF_EMS_MAX_ONTLAAD_W, CONF_EMS_ZEKERING_A):
                self._opties[sleutel] = details.get(sleutel)
            if self._opties[CONF_EMS_AAN] and not self._opties.get(CONF_EMS_VERMOGEN):
                return self._toon_batterij(self._opties, {"base": "geen_vermogen"})
            if self._heeft_keuzes():
                return await self.async_step_standen()
            return await self._volgende("batterij")

        standaard = dict(self._opties)
        # Een Deye of Sunsynk met tijdprogramma's: dat profiel voorstellen, ook als er al iets stond dat niet werkte.
        if not standaard.get(CONF_EMS_PROFIEL):
            standaard[CONF_EMS_PROFIEL] = PROFIEL_DEYE if herkennen.zoek_deye(self._entiteiten or []) else PROFIEL_VERMOGEN
        sturing = herkennen.zoek_sturing(self._entiteiten or [], self._huidige_sensoren())
        if sturing and not standaard.get(CONF_EMS_VERMOGEN) and standaard[CONF_EMS_PROFIEL] == PROFIEL_VERMOGEN:
            standaard.update({
                CONF_EMS_VERMOGEN: sturing.vermogen, CONF_EMS_ONTLAAD: sturing.ontlaad, CONF_EMS_NETDOEL: sturing.soort == "netdoel",
                CONF_EMS_LADEN_POSITIEF: sturing.laden_positief, CONF_EMS_MODUS: sturing.modus,
                CONF_EMS_MODUS_STUREN: sturing.modus_sturen, CONF_EMS_MODUS_TERUG: sturing.modus_terug,
                CONF_EMS_RICHTING: sturing.richting, CONF_EMS_RICHTING_LADEN: sturing.richting_laden,
                CONF_EMS_RICHTING_ONTLADEN: sturing.richting_ontladen, CONF_EMS_RICHTING_STOP: sturing.richting_stop,
            })
        return self._toon_batterij(standaard)

    def _huidige_sensoren(self) -> herkennen.Sensoren:
        return herkennen.Sensoren(batterij=self._opties.get(CONF_BATTERIJ) or (self._sensoren.batterij if self._sensoren else None),
                                  soc=self._opties.get(CONF_SOC) or (self._sensoren.soc if self._sensoren else None))

    def _toon_batterij(self, standaard: dict, fouten: dict | None = None) -> ConfigFlowResult:
        if standaard.get(CONF_EMS_PROFIEL) == PROFIEL_DEYE:
            uitleg = ("Dit lijkt een **Deye of Sunsynk**: die stuurt Zonneburen via de zes tijdprogramma's (Time of Use), "
                      "want een vermogen in W kent hij niet. Kies dat hieronder; in de volgende stap zie je de programma's.")
        elif standaard.get(CONF_EMS_VERMOGEN):
            uitleg = self._uitleg_sturing(standaard)
        else:
            uitleg = "Er is geen batterij gevonden die Home Assistant kan sturen. Heb je er wel een, kies dan zelf de entiteiten onder **Details**."
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="batterij", data_schema=_batterij_schema(standaard), errors=fouten or {},
            description_placeholders={"uitleg": uitleg})

    def _uitleg_sturing(self, s: dict) -> str:
        def naam(entity_id: str | None) -> str:
            staat = self.hass.states.get(entity_id) if entity_id else None
            return f"**{staat.name if staat else entity_id}**"
        if s.get(CONF_EMS_NETDOEL):
            regels = [f"Zonneburen stelt het gewenste vermogen op het net in via {naam(s[CONF_EMS_VERMOGEN])}."]
        elif s.get(CONF_EMS_ONTLAAD):
            regels = [f"Laden via {naam(s[CONF_EMS_VERMOGEN])}, ontladen via {naam(s[CONF_EMS_ONTLAAD])}."]
            if s.get(CONF_EMS_RICHTING):
                regels.append(f"De richting kiest Zonneburen met {naam(s[CONF_EMS_RICHTING])}.")
        else:
            regels = [f"Zonneburen stelt het batterijvermogen in via {naam(s[CONF_EMS_VERMOGEN])}."]
        regels.append(self._bereik(s[CONF_EMS_VERMOGEN]))
        if s.get(CONF_EMS_MODUS):
            regels.append(f"Voor het sturen zet het de batterij met {naam(s[CONF_EMS_MODUS])} in de juiste stand, en daarna terug.")
        return " ".join(regels)

    def _bereik(self, entity_id: str | None) -> str:
        """De live waarde en het bereik van het gekozen getal, zodat een verkeerde keuze opvalt."""
        staat = self.hass.states.get(entity_id) if entity_id else None
        if staat is None:
            return ""
        eenheid = staat.attributes.get("unit_of_measurement") or ""
        laag, hoog = _getal(staat.attributes.get("min")), _getal(staat.attributes.get("max"))
        tekst = f"Nu {staat.state} {eenheid}".rstrip() + (f", bereik {laag:g} tot {hoog:g} {eenheid}".rstrip() if laag is not None and hoog is not None else "") + "."
        factor = 1000 if eenheid == "kW" else 1
        if hoog is not None and max(abs(hoog), abs(laag or 0)) * factor < herkennen.MIN_BEREIK_W:
            tekst += " **Let op:** dit bereik is te klein voor een batterij. Is dit misschien een teruglevergrens?"
        return tekst

    async def async_step_deye(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """De tijdprogramma's van een Deye of Sunsynk: automatisch ingevuld, te overschrijven."""
        await self._herken()
        if user_input is not None:
            for sleutel in (CONF_TOU, CONF_TOU_LADEN, CONF_TOU_SOC, CONF_TOU_TIJD, CONF_TOU_STROOM, CONF_TOU_SPANNING, CONF_TOU_MIN_SOC,
                            CONF_TOU_MAX_PCT, CONF_TOU_MIN_PCT, CONF_EMS_ZEKERING_A, CONF_TOU_WERKMODUS, CONF_TOU_PATROON,
                            CONF_TOU_VERMOGEN, CONF_TOU_MAX_LAAD_A, CONF_TOU_MAX_ONTLAAD_A, CONF_TOU_NETLADEN, CONF_TOU_MAX_EXPORT,
                            CONF_TOU_MAX_LAAD_W, CONF_TOU_MAX_ONTLAAD_W, CONF_TOU_TERUGLEVEREN, CONF_TOU_FASEN):
                self._opties[sleutel] = user_input.get(sleutel)
            if self._opties.get(CONF_EMS_AAN) and not (user_input.get(CONF_TOU_LADEN) and user_input.get(CONF_TOU_SOC)):
                return self._toon_deye(self._opties, {"base": "geen_programmas"})
            return await self._volgende("batterij")
        standaard = dict(self._opties)
        if not standaard.get(CONF_TOU_LADEN) and (deye := herkennen.zoek_deye(self._entiteiten or [])):
            standaard.update(deye_opties(deye))
        return self._toon_deye(standaard)

    def _toon_deye(self, standaard: dict, fouten: dict | None = None) -> ConfigFlowResult:
        regels = []
        for sleutel, naam in ((CONF_TOU, "Time of Use"), (CONF_TOU_SPANNING, "Accuspanning"), (CONF_TOU_MIN_SOC, "Ondergrens omvormer"),
                              (CONF_TOU_STROOM, "Laadstroom van het net")):
            staat = self.hass.states.get(standaard.get(sleutel) or "")
            if staat:
                regels.append(f"- **{naam}:** {staat.state} {staat.attributes.get('unit_of_measurement') or ''}".rstrip())
        laden = standaard.get(CONF_TOU_LADEN) or []
        if laden:
            regels.append(f"- **Programma's:** {len(laden)} gevonden")
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="deye", data_schema=_deye_schema(standaard), errors=fouten or {},
            description_placeholders={"gevonden": "\n".join(regels) or "- Nog niets gevonden: kies hieronder de entiteiten."})

    def _heeft_keuzes(self) -> bool:
        return bool(self._opties.get(CONF_EMS_RICHTING)) or protocol.is_domein(self._opties.get(CONF_EMS_MODUS), "select", "input_select")

    def _opties_van(self, entity_id: str | None) -> list[str]:
        staat = self.hass.states.get(entity_id) if entity_id else None
        return [str(o) for o in (staat.attributes.get("options") or [])] if staat else []

    async def async_step_standen(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            for sleutel in (CONF_EMS_MODUS_STUREN, CONF_EMS_MODUS_TERUG, CONF_EMS_RICHTING_LADEN, CONF_EMS_RICHTING_ONTLADEN,
                            CONF_EMS_RICHTING_STOP):
                self._opties[sleutel] = user_input.get(sleutel)
            return await self._volgende("batterij")

        velden: dict = {}
        if protocol.is_domein(self._opties.get(CONF_EMS_MODUS), "select", "input_select"):
            opties = self._opties_van(self._opties[CONF_EMS_MODUS])
            gekozen = herkennen.kies_opties(opties)
            for sleutel, soort in ((CONF_EMS_MODUS_STUREN, "sturen"), (CONF_EMS_MODUS_TERUG, "terug")):
                extra, kiezer = _keuze(opties, self._opties.get(sleutel) or gekozen.get(soort))
                velden[vol.Optional(sleutel, **extra)] = kiezer
        if self._opties.get(CONF_EMS_RICHTING):
            opties = self._opties_van(self._opties[CONF_EMS_RICHTING])
            gekozen = herkennen.kies_opties(opties)
            for sleutel, soort in ((CONF_EMS_RICHTING_LADEN, "laden"), (CONF_EMS_RICHTING_ONTLADEN, "ontladen"),
                                   (CONF_EMS_RICHTING_STOP, "stop")):
                extra, kiezer = _keuze(opties, self._opties.get(sleutel) or gekozen.get(soort))
                velden[vol.Optional(sleutel, **extra)] = kiezer
        return self.async_show_form(step_id="standen", data_schema=vol.Schema(velden))  # type: ignore[attr-defined]


class ZonneburenConfigFlow(_Stappen, ConfigFlow, domain=DOMAIN):
    VERSION = 1
    # 1.2: het sturingsprofiel (vermogen of Deye-tijdprogramma's); zie async_migrate_entry.
    MINOR_VERSION = 3

    def __init__(self) -> None:
        self._gekoppeld: dict = {}
        self._opties: dict = {}

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

    async def _volgende(self, na: str) -> ConfigFlowResult:
        if na == "sensoren":
            return await self.async_step_apparaten()
        if na == "apparaten" and (herkennen.zoek_sturing(self._entiteiten or [], self._huidige_sensoren())
                                  or herkennen.zoek_deye(self._entiteiten or [])):
            return await self.async_step_batterij()
        return self.async_create_entry(title=self._gekoppeld[CONF_ADRES], data=self._gekoppeld, options=self._opties)

    @staticmethod
    @callback
    def async_get_options_flow(_entry: ConfigEntry) -> OptionsFlow:
        return ZonneburenOptionsFlow()


class ZonneburenOptionsFlow(_Stappen, OptionsFlow):
    """Configureren: een menu met dezelfde stappen. Wat de webapp instelt, blijft staan."""

    def __init__(self) -> None:
        self._opties: dict = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._opties = dict(self.config_entry.options)
        return self.async_show_menu(step_id="init", menu_options=["sensoren", "apparaten", "batterij"])

    async def async_step_apparaten(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self._herken()
        if user_input is None and not self._voorstellen():
            return self.async_abort(reason="geen_apparaten")
        return await super().async_step_apparaten(user_input)

    async def _volgende(self, na: str) -> ConfigFlowResult:
        return self.async_create_entry(data=self._opties)
