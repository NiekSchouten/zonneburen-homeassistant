"""De kaarten en het paneel Energieplan, geleverd door de integratie zelf.

Home Assistant serveert ze lokaal: ze werken ook als het internet weg is, en de bewoner hoeft geen
resource toe te voegen. Het paneel staat in de zijbalk en zoekt zelf de sensoren van Zonneburen.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import VERSIE

_LOGGER = logging.getLogger(__name__)

URL = "/zonneburen_static/zonneburen-card.js"
PANEEL = "zonneburen-energieplan"
_KLAAR = "zonneburen_frontend"
_ROUTE = "zonneburen_route"


async def registreer(hass: HomeAssistant) -> None:
    """Eén keer per Home Assistant, ook bij meer huizen. Zonder frontend (tests, kale installatie) niets."""
    if hass.data.get(_KLAAR) or getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return
    hass.data[_KLAAR] = True
    try:
        from homeassistant.components import frontend, panel_custom
        from homeassistant.components.http import StaticPathConfig

        if not hass.data.get(_ROUTE):
            # Een route kan niet weg; bij herladen alleen het paneel opnieuw.
            bestand = Path(__file__).parent / "www" / "zonneburen-card.js"
            await hass.http.async_register_static_paths([StaticPathConfig(URL, str(bestand), False)])
            hass.data[_ROUTE] = True
        versie_url = f"{URL}?v={VERSIE}"
        # Zo staan de kaarten in elk dashboard klaar, zonder resource.
        frontend.add_extra_js_url(hass, versie_url)
        await panel_custom.async_register_panel(
            hass, frontend_url_path=PANEEL, webcomponent_name="zonneburen-energieplan-panel",
            sidebar_title="Energieplan", sidebar_icon="mdi:battery-clock", module_url=versie_url,
            embed_iframe=False, require_admin=False,
        )
    except Exception as fout:  # noqa: BLE001 - zonder paneel werkt de integratie gewoon
        _LOGGER.warning("Paneel Energieplan niet geregistreerd: %s", fout)


def verwijder(hass: HomeAssistant) -> None:
    """Als het laatste huis weg is: het paneel uit de zijbalk. De statische route blijft tot een herstart."""
    if not hass.data.get(_KLAAR):
        return
    try:
        from homeassistant.components import frontend

        frontend.async_remove_panel(hass, PANEEL)
        frontend.remove_extra_js_url(hass, f"{URL}?v={VERSIE}")
    except Exception:  # noqa: BLE001
        pass
    hass.data.pop(_KLAAR, None)
