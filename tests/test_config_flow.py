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


async def test_vult_zelf_in_en_neemt_apparaten_en_batterij_mee(hass):
    """Een huis met een P1-meter, een thuisbatterij met een stand en een boiler: alles wordt voorgesteld."""
    hass.states.async_set("sensor.p1_meter_power", "350", {"unit_of_measurement": "W", "device_class": "power",
                                                          "friendly_name": "P1 meter power"})
    hass.states.async_set("sensor.battery_power", "-800", {"unit_of_measurement": "W", "device_class": "power",
                                                          "friendly_name": "Battery power"})
    hass.states.async_set("number.battery_charge_discharge_power", "0", {
        "unit_of_measurement": "W", "min": -2400, "max": 2400, "friendly_name": "Battery charge discharge power"})
    hass.states.async_set("select.battery_work_mode", "Self use", {"options": ["Self use", "Remote control"],
                                                                  "friendly_name": "Battery work mode"})
    hass.states.async_set("water_heater.boiler", "eco", {"temperature": 55, "max_temp": 75, "friendly_name": "Boiler"})

    resultaat = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch("custom_components.zonneburen.config_flow.koppel", return_value=GEKOPPELD), \
            patch("custom_components.zonneburen.async_setup_entry", return_value=True):
        resultaat = await hass.config_entries.flow.async_configure(
            resultaat["flow_id"], {"server": "https://zonopzwaagdijk.nl", "code": "abc123"})
        assert resultaat["step_id"] == "sensoren"
        gevonden = resultaat["description_placeholders"]["gevonden"]
        assert "350 W afname" in gevonden and "800 W laden" in gevonden

        resultaat = await hass.config_entries.flow.async_configure(
            resultaat["flow_id"], {"net": "sensor.p1_meter_power", "batterij": "sensor.battery_power"})
        assert resultaat["step_id"] == "apparaten"

        resultaat = await hass.config_entries.flow.async_configure(resultaat["flow_id"], {"kies": ["water_heater.boiler"]})
        assert resultaat["step_id"] == "batterij"
        assert "Battery charge discharge power" in resultaat["description_placeholders"]["uitleg"]

        resultaat = await hass.config_entries.flow.async_configure(resultaat["flow_id"], {"ems_aan": True, "details": {
            "ems_vermogen": "number.battery_charge_discharge_power", "ems_laden_positief": True,
            "ems_modus": "select.battery_work_mode"}})
        assert resultaat["step_id"] == "standen"

        resultaat = await hass.config_entries.flow.async_configure(
            resultaat["flow_id"], {"ems_modus_sturen": "Remote control", "ems_modus_terug": "Self use"})

    assert resultaat["type"] is FlowResultType.CREATE_ENTRY
    opties = resultaat["options"]
    assert opties["ems_aan"] and opties["ems_vermogen"] == "number.battery_charge_discharge_power"
    assert (opties["ems_modus_sturen"], opties["ems_modus_terug"]) == ("Remote control", "Self use")
    boiler = opties["apparaten"][0]
    assert boiler["host"] == "water_heater.boiler" and boiler["enabled"] and boiler["normalTemp"] == 55 and boiler["boostTemp"] == 65


async def test_configureren_heeft_een_menu(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, data={"adres": "De Kreekrug 9"}, options={"net": "sensor.p1"})
    entry.add_to_hass(hass)
    resultaat = await hass.config_entries.options.async_init(entry.entry_id)
    assert resultaat["type"] is FlowResultType.MENU
    resultaat = await hass.config_entries.options.async_configure(resultaat["flow_id"], {"next_step_id": "apparaten"})
    assert resultaat["type"] is FlowResultType.ABORT and resultaat["reason"] == "geen_apparaten"


def test_subapparaat_zonder_waarschuwing_van_home_assistant():
    """Een ChildDeviceEntry kent alleen parent_device_id; naar via_device_id, fabrikant of model vragen waarschuwt."""
    from unittest.mock import MagicMock

    from homeassistant.helpers import device_registry as dr

    from custom_components.zonneburen.config_flow import _echt_apparaat

    class Kind:
        __slots__ = ("parent_device_id",)

        def __init__(self, ouder):
            self.parent_device_id = ouder

        def __getattr__(self, naam):
            raise AssertionError(f"vroeg naar {naam}")

    ouder = MagicMock(spec=dr.DeviceEntry, manufacturer="Daikin", model="Altherma")
    register = MagicMock()
    register.async_get = lambda device_id: ouder if device_id == "ouder" else None
    assert _echt_apparaat(register, Kind("ouder")) is ouder
    assert _echt_apparaat(register, ouder) is ouder
    assert _echt_apparaat(register, None) is None
