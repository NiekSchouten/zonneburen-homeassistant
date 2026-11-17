"""Zelf herkennen van sensoren, batterijsturing en apparaten, zonder Home Assistant."""

from custom_components.zonneburen.herkennen import (
    Entiteit, kies_opties, zoek_apparaten, zoek_sensoren, zoek_sturing,
)


def W(entity_id, platform, apparaat, naam="", sleutel="", eenheid="W", staat="100", **rest):
    return Entiteit(entity_id, platform=platform, apparaat=apparaat, naam=naam, sleutel=sleutel, device_class="power",
                    eenheid=eenheid, staat=staat, **rest)


def test_homewizard_p1_en_geen_stekker():
    entiteiten = [
        W("sensor.stekker_vermogen", "homewizard", "stekker", "Stekker Vermogen", "active_power_w", apparaatnaam="Wasmachine",
          model="HWE-SKT"),
        W("sensor.p1_meter_vermogen", "homewizard", "p1", "P1 meter Vermogen", "active_power_w", apparaatnaam="P1 meter",
          model="HWE-P1"),
        W("sensor.p1_meter_vermogen_fase_1", "homewizard", "p1", "P1 meter Vermogen fase 1", "active_power_l1_w", model="HWE-P1"),
    ]
    gevonden = zoek_sensoren(entiteiten)
    assert gevonden.net == "sensor.p1_meter_vermogen"
    assert gevonden.net_terug is None and not gevonden.net_om


def test_dsmr_met_afname_en_teruglevering_apart():
    entiteiten = [
        W("sensor.electricity_meter_power_consumption", "dsmr", "meter", "Power consumption", "current_electricity_usage", "kW"),
        W("sensor.electricity_meter_power_production", "dsmr", "meter", "Power production", "current_electricity_delivery", "kW"),
        W("sensor.electricity_meter_power_consumption_phase_l1", "dsmr", "meter", "Power consumption phase L1",
          "instantaneous_active_power_l1_positive", "kW"),
    ]
    gevonden = zoek_sensoren(entiteiten)
    assert gevonden.net == "sensor.electricity_meter_power_consumption"
    assert gevonden.net_terug == "sensor.electricity_meter_power_production"


def test_energiedashboard_gaat_voor_en_neemt_het_teken_mee():
    entiteiten = [W("sensor.grid_power", "template", None), W("sensor.p1_vermogen", "homewizard", "p1", sleutel="active_power_w",
                                                                 model="HWE-P1"),
                  W("sensor.zon", "template", None), W("sensor.accu", "template", None)]
    voorkeuren = {"energy_sources": [
        {"type": "grid", "flow_from": [{"stat_energy_from": "sensor.import_kwh"}], "flow_to": [],
         "power": [{"stat_rate": "sensor.grid_power", "stat_rate_negate": True}]},
        {"type": "solar", "stat_energy_from": "sensor.zon_kwh", "stat_rate": "sensor.zon"},
        {"type": "battery", "stat_energy_from": "sensor.uit", "stat_energy_to": "sensor.in", "stat_rate": "sensor.accu"},
    ]}
    gevonden = zoek_sensoren(entiteiten, voorkeuren)
    assert (gevonden.net, gevonden.net_om) == ("sensor.grid_power", True)
    assert gevonden.zon == "sensor.zon" and gevonden.batterij == "sensor.accu" and not gevonden.batterij_om
    assert gevonden.bron["net"] == "dashboard"


def test_zon_op_het_apparaat_uit_het_energiedashboard():
    entiteiten = [
        W("sensor.solaredge_ac_power", "solaredge_modbus_multi", "omvormer", "SolarEdge I1 AC Power"),
        Entiteit("sensor.solaredge_ac_energy_kwh", platform="solaredge_modbus_multi", apparaat="omvormer", eenheid="kWh"),
        W("sensor.solaredge_m1_ac_power", "solaredge_modbus_multi", "meter", "SolarEdge M1 AC Power"),
    ]
    voorkeuren = {"energy_sources": [{"type": "solar", "stat_energy_from": "sensor.solaredge_ac_energy_kwh"}]}
    assert zoek_sensoren(entiteiten, voorkeuren).zon == "sensor.solaredge_ac_power"


def test_laadniveau_van_de_batterij_niet_van_de_telefoon():
    entiteiten = [
        Entiteit("sensor.telefoon_battery_level", platform="mobile_app", apparaat="tel", device_class="battery", eenheid="%"),
        Entiteit("sensor.deurbel_batterij", platform="zha", apparaat="deur", device_class="battery", eenheid="%"),
        W("sensor.victron_battery_power", "victron", "accu", "Battery power"),
        Entiteit("sensor.victron_battery_soc", platform="victron", apparaat="accu", device_class="battery", eenheid="%",
                 naam="Battery state of charge"),
    ]
    gevonden = zoek_sensoren(entiteiten)
    assert gevonden.batterij == "sensor.victron_battery_power" and gevonden.batterij_om   # Victron: positief is laden
    assert gevonden.soc == "sensor.victron_battery_soc"


def test_geen_laadniveau_zonder_batterij():
    entiteiten = [Entiteit("sensor.deurbel_batterij", platform="zha", apparaat="deur", device_class="battery", eenheid="%")]
    assert zoek_sensoren(entiteiten).soc is None


def test_victron_netdoel():
    entiteiten = [
        W("sensor.victron_battery_power", "victron", "gx", "Battery power"),
        Entiteit("number.victron_ess_grid_setpoint", platform="victron", apparaat="gx", naam="ESS grid setpoint", eenheid="W",
                 minimum=-10000, maximum=10000),
    ]
    sturing = zoek_sturing(entiteiten, zoek_sensoren(entiteiten))
    assert sturing.soort == "netdoel" and sturing.vermogen == "number.victron_ess_grid_setpoint"


def test_laden_en_ontladen_apart_met_richting_en_rs485():
    entiteiten = [
        W("sensor.venus_battery_power", "marstek_venus_modbus", "venus", "Battery Power"),
        Entiteit("sensor.venus_battery_soc", platform="marstek_venus_modbus", apparaat="venus", eenheid="%", device_class="battery"),
        Entiteit("number.venus_forcible_charge_power", platform="marstek_venus_modbus", apparaat="venus",
                 naam="Forcible Charge Power", eenheid="W", minimum=0, maximum=2500),
        Entiteit("number.venus_forcible_discharge_power", platform="marstek_venus_modbus", apparaat="venus",
                 naam="Forcible Discharge Power", eenheid="W", minimum=0, maximum=2500),
        Entiteit("number.venus_charging_cutoff_capacity", platform="marstek_venus_modbus", apparaat="venus",
                 naam="Charging Cutoff Capacity", eenheid="%"),
        Entiteit("select.venus_force_mode", platform="marstek_venus_modbus", apparaat="venus", naam="Force Mode",
                 opties=["Stop", "Charge", "Discharge"]),
        Entiteit("switch.venus_rs485_control_mode", platform="marstek_venus_modbus", apparaat="venus", naam="RS485 Control Mode"),
    ]
    sturing = zoek_sturing(entiteiten, zoek_sensoren(entiteiten))
    assert sturing.soort == "apart"
    assert (sturing.vermogen, sturing.ontlaad) == ("number.venus_forcible_charge_power", "number.venus_forcible_discharge_power")
    assert (sturing.richting, sturing.richting_laden, sturing.richting_ontladen, sturing.richting_stop) == \
        ("select.venus_force_mode", "Charge", "Discharge", "Stop")
    assert sturing.modus == "switch.venus_rs485_control_mode"


def test_solaredge_standen_kiezen_de_beste_optie():
    assert kies_opties(["Disabled", "Maximize Self Consumption", "Time of Use", "Backup Only", "Remote Control"]) == \
        {"terug": "Maximize Self Consumption", "sturen": "Remote Control"}
    gekozen = kies_opties(["Solar Power Only (Off)", "Charge from Clipped Solar Power", "Charge from Solar Power",
                           "Charge from Solar Power and Grid", "Discharge to Maximize Export", "Discharge to Minimize Import",
                           "Maximize Self Consumption"])
    assert gekozen["laden"] == "Charge from Solar Power and Grid"
    assert gekozen["stop"] == "Solar Power Only (Off)"
    assert gekozen["ontladen"].startswith("Discharge")


def test_een_getal_met_teken_en_een_stand():
    entiteiten = [
        W("sensor.accu_vermogen", "zendure_ha", "accu", "Battery power"),
        Entiteit("number.accu_charge_discharge_power", platform="zendure_ha", apparaat="accu", naam="Charge discharge power",
                 eenheid="W", minimum=-2400, maximum=2400),
        Entiteit("select.accu_operation", platform="zendure_ha", apparaat="accu", naam="Operation mode",
                 opties=["Off", "Manual", "Smart"]),
    ]
    sturing = zoek_sturing(entiteiten, zoek_sensoren(entiteiten))
    assert sturing.soort == "vermogen" and sturing.laden_positief
    assert (sturing.modus, sturing.modus_sturen, sturing.modus_terug) == ("select.accu_operation", "Manual", "Smart")


def test_niets_te_sturen():
    assert zoek_sturing([W("sensor.p1", "homewizard", "p1")]) is None


def test_apparaten_vinden_met_vermogen_en_huidige_stand():
    entiteiten = [
        Entiteit("climate.quatt", platform="quatt", apparaat="wp", apparaatnaam="Quatt", staat="heat", doeltemp=20.5, max_temp=28,
                 hvac_modi=["heat", "off"]),
        W("sensor.quatt_power", "quatt", "wp", "Quatt power"),
        Entiteit("water_heater.boiler", platform="ariston", apparaat="b", apparaatnaam="Boiler", staat="eco", doeltemp=55,
                 max_temp=75),
        Entiteit("switch.alfen_charging", platform="alfen_wallbox", apparaat="lp", apparaatnaam="Alfen Eve", naam="Charging",
                 staat="off"),
        Entiteit("switch.alfen_lock", platform="alfen_wallbox", apparaat="lp", naam="Cable lock", staat="on"),
        Entiteit("climate.woonkamer_radiator", platform="zha", apparaat="trv", naam="Radiator", staat="heat",
                 hvac_modi=["heat", "off"]),
        Entiteit("switch.keukenlamp", platform="hue", apparaat="lamp", staat="on"),
    ]
    voorstellen = {v.entity_id: v for v in zoek_apparaten(entiteiten)}
    assert set(voorstellen) == {"climate.quatt", "water_heater.boiler", "switch.alfen_charging"}
    wp = voorstellen["climate.quatt"]
    assert (wp.soort, wp.vermogen, wp.normaal_aan, wp.normaal_temp, wp.boost_temp) == ("Warmtepomp", "sensor.quatt_power", True, 20, 21)
    assert voorstellen["water_heater.boiler"].boost_temp == 65
    laadpaal = voorstellen["switch.alfen_charging"]
    assert laadpaal.soort == "Laadpaal" and not laadpaal.normaal_aan
    assert laadpaal.als_apparaat()["type"] == 6 and laadpaal.als_apparaat()["wijk"]


def test_al_gekozen_apparaten_niet_opnieuw():
    entiteiten = [Entiteit("water_heater.boiler", platform="ariston", apparaat="b", staat="eco", doeltemp=55)]
    assert zoek_apparaten(entiteiten, {"water_heater.boiler"}) == []
