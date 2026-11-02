"""Historie uit de recorder van Home Assistant.

Zo begint Inzichten niet leeg op de dag dat je de integratie installeert: Home Assistant heeft je
zon, net en batterij al bijgehouden.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant

from .const import CONF_BATTERIJ, CONF_BATTERIJ_OM, CONF_NET, CONF_NET_OM, CONF_SOC, CONF_VERBRUIK, CONF_ZON

VELD_VAN = {CONF_NET: "net", CONF_ZON: "pv", CONF_BATTERIJ: "bat", CONF_SOC: "soc", CONF_VERBRUIK: "load"}


async def lees(hass: HomeAssistant, opties: dict, start: datetime, eind: datetime, periode: str) -> list[tuple[float, dict[str, float]]]:
    """Gemiddelden per periode ("5minute" of "hour") van de gekozen sensoren, in W, gesorteerd op tijd."""
    sensoren = {opties[sleutel]: veld for sleutel, veld in VELD_VAN.items() if opties.get(sleutel)}
    if not sensoren or "recorder" not in hass.config.components:
        return []

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    rijen = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, eind, set(sensoren), periode, {"power": UnitOfPower.WATT}, {"mean"})

    per_tijd: dict[float, dict[str, float]] = {}
    for sensor, lijst in rijen.items():
        veld = sensoren.get(sensor)
        if veld is None:
            continue
        for rij in lijst:
            if rij.get("mean") is None:
                continue
            begin = rij["start"]
            tijd = begin.timestamp() if isinstance(begin, datetime) else float(begin)
            waarde = float(rij["mean"])
            if (veld == "net" and opties.get(CONF_NET_OM)) or (veld == "bat" and opties.get(CONF_BATTERIJ_OM)):
                waarde = -waarde
            per_tijd.setdefault(tijd, {})[veld] = waarde
    return sorted(per_tijd.items())
