"""Koppelen met een code en daarna de sensoren kiezen."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.zonneburen.const import DOMAIN
from custom_components.zonneburen.koppelen import KoppelFout

GEKOPPELD = {"adres": "De Kreekrug 9", "host": "zonopzwaagdijk.nl", "poort": 8883, "gebruiker": "huis-abc", "wachtwoord": "geheim"}


async def test_koppelen_en_sensoren_kiezen(hass):
    resultaat = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert resultaat["type"] is FlowResultType.FORM

    with patch("custom_components.zonneburen.config_flow.koppel", return_value=GEKOPPELD) as koppel, \
            patch("custom_components.zonneburen.async_setup_entry", return_value=True):
        resultaat = await hass.config_entries.flow.async_configure(
            resultaat["flow_id"], {"server": "https://zonopzwaagdijk.nl/", "code": "abc123"})
        assert resultaat["step_id"] == "sensoren"
        assert koppel.call_args.args[3].startswith("ha-")

        resultaat = await hass.config_entries.flow.async_configure(
            resultaat["flow_id"], {"net": "sensor.p1_vermogen", "zon": "sensor.zon"})

    assert resultaat["type"] is FlowResultType.CREATE_ENTRY
    assert resultaat["title"] == "De Kreekrug 9"
    assert resultaat["data"]["server"] == "https://zonopzwaagdijk.nl"
    assert resultaat["data"]["mqtt_wachtwoord"] == "geheim"
    assert resultaat["options"]["net"] == "sensor.p1_vermogen"
    assert resultaat["options"]["wijk_automatisch"] is True


async def test_onbekende_code_geeft_een_melding(hass):
    resultaat = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch("custom_components.zonneburen.config_flow.koppel", side_effect=KoppelFout("code_onbekend")):
        resultaat = await hass.config_entries.flow.async_configure(
            resultaat["flow_id"], {"server": "https://zonopzwaagdijk.nl", "code": "fout"})
    assert resultaat["type"] is FlowResultType.FORM
    assert resultaat["errors"] == {"base": "code_onbekend"}
