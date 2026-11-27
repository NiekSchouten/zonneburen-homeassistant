"""Zelf uitzoeken welke sensoren, batterij-instellingen en apparaten een huis heeft.

Bewust zonder Home Assistant, zodat dit los te testen is: de config flow geeft een lijst met
`Entiteit`-en en de voorkeuren van het Energie-dashboard mee. De volgorde van zoeken:

1. het Energie-dashboard: wie daar zijn vermogenssensoren heeft ingesteld, heeft het goede teken al;
2. bekende merkintegraties, zoals HomeWizard, DSMR, Victron of Marstek;
3. trefwoorden in de naam, op hetzelfde apparaat als de energiesensoren uit het dashboard.

Alles is een voorstel: de bewoner ziet het ingevuld in het formulier en kan het aanpassen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ROL_BOILER, ROL_LAADPAAL, ROL_AIRCO, ROL_ANDERS = 0, 1, 2, 3


@dataclass
class Entiteit:
    entity_id: str
    platform: str = ""
    apparaat: str | None = None
    apparaatnaam: str = ""
    merk: str = ""
    model: str = ""
    naam: str = ""
    sleutel: str = ""            # translation_key en unique_id: stabieler dan de naam
    device_class: str | None = None
    eenheid: str | None = None
    staat: str | None = None
    opties: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    hvac_modi: list[str] = field(default_factory=list)
    doeltemp: float | None = None
    max_temp: float | None = None

    @property
    def domein(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def tekst(self) -> str:
        """Alles waarop we zoeken, in kleine letters en met spaties in plaats van _ en -."""
        return _plat(f"{self.entity_id.split('.', 1)[-1]} {self.naam} {self.sleutel}")

    @property
    def apparaattekst(self) -> str:
        return _plat(f"{self.platform} {self.apparaatnaam} {self.merk} {self.model}")

    @property
    def is_vermogen(self) -> bool:
        return self.domein == "sensor" and (self.device_class == "power" or self.eenheid in ("W", "kW"))

    def getal(self) -> float | None:
        try:
            waarde = float(self.staat)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return waarde * 1000 if self.eenheid == "kW" else waarde


def _plat(tekst: str) -> str:
    return " " + re.sub(r"[_\-./]+", " ", tekst.lower()) + " "


def _heeft(tekst: str, *woorden: str) -> bool:
    return any(woord in tekst for woord in woorden)


# ---------------------------------------------------------------------------------------------- Sensoren

@dataclass
class Sensoren:
    net: str | None = None
    net_terug: str | None = None
    net_om: bool = False
    zon: str | None = None
    batterij: str | None = None
    batterij_om: bool = False
    soc: str | None = None
    verbruik: str | None = None
    bron: dict[str, str] = field(default_factory=dict)   # per sleutel: hoe gevonden (dashboard, merk, naam)


# Slimme meters waarvan we precies weten welke sensor wat is. Eén sensor: positief is afname.
# Twee sensoren: afname en teruglevering apart, allebei positief.
METERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "homewizard": ((" active power w ", " active power "), ()),
    "dsmr": ((" current electricity usage ", " power consumption "), (" current electricity delivery ", " power production ")),
    "dsmr_reader": ((" electricity currently delivered ",), (" electricity currently returned ",)),
    "p1_monitor": ((" power consumption ",), (" power production ",)),
    "enphase_envoy": ((" net consumption ", " current net power consumption "), ()),
}

# Merken waarvan de batterijsensor positief is bij laden; Zonneburen rekent met positief = ontladen.
BATTERIJ_LADEN_POSITIEF = ("huawei_solar", "victron", "victron_mqtt", "victron_ble", "solaredge_modbus_multi")

NET_WOORDEN = (" grid ", " net power ", " netto ", " p1 ", " smart meter ", " slimme meter ", " meter power ")
# Omvormers waarvan het vermogen vaak gewoon "AC power" of "active power" heet.
ZON_MERKEN = ("solaredge", "solaredge_modbus_multi", "sma", "fronius", "goodwe", "huawei_solar", "growatt_server", "growatt_local",
              "enphase_envoy", "apsystems", "apsystems_ecur", "hoymiles_wifi", "opendtu", "solarman", "solax", "foxess", "sungrow",
              "omnik_inverter", "zeversolar", "solis", "solis_modbus", "sunsynk", "deye", "solarlog", "fusion_solar", "saj_modbus")
ZON_WOORDEN = (" pv ", " solar ", " zon ", " zonne", " inverter ", " omvormer ", " production ", " productie ", " opwek", " ppv ")
BAT_WOORDEN = (" battery ", " batterij ", " accu ", " bat ", " battery1 ", " storage ")
VERBRUIK_WOORDEN = (" house consumption ", " home consumption ", " load power ", " huisverbruik ", " house load ",
                    " consumption power ", " current power consumption ", " verbruik huis ")
NIET_NU = (" today ", " vandaag ", " total ", " totaal ", " daily ", " month ", " year ", " energy ", " limit ", " max ",
           " forecast ", " voorspelling ", " average ", " gemiddeld ")


def uit_energiedashboard(voorkeuren: dict | None) -> tuple[dict[str, list[tuple[str, bool]]], dict[str, set[str]]]:
    """Uit de voorkeuren van het Energie-dashboard: vermogenssensoren per soort en de energiesensoren.

    Vermogen: {"net": [(id, omdraaien)], "net_af": [...], "net_terug": [...], "zon": [...], "batterij": [...]}.
    Sinds 2025.12 kan het dashboard vermogens tonen, met dezelfde tekens als Zonneburen: het net
    positief bij afname, de batterij positief bij ontladen. Oudere versies hebben alleen energie (kWh).
    """
    vermogen: dict[str, list[tuple[str, bool]]] = {}
    energie: dict[str, set[str]] = {"net": set(), "zon": set(), "batterij": set()}

    def loop(knoop, soort: str, om: bool = False) -> None:
        if isinstance(knoop, list):
            for deel in knoop:
                loop(deel, soort, om)
            return
        if not isinstance(knoop, dict):
            return
        om = om or bool(knoop.get("stat_rate_negate") or knoop.get("stat_rate_inverted_negate"))
        for sleutel, waarde in knoop.items():
            if isinstance(waarde, (dict, list)):
                loop(waarde, soort, om)
            elif not isinstance(waarde, str) or not waarde.startswith("sensor."):
                continue
            elif sleutel.startswith("stat_energy"):
                energie.setdefault(soort, set()).add(waarde)
            elif sleutel == "stat_rate":
                vermogen.setdefault(soort, []).append((waarde, om))
            elif sleutel == "stat_rate_inverted":
                vermogen.setdefault(soort, []).append((waarde, not om))
            elif sleutel == "stat_rate_from":
                vermogen.setdefault(f"{soort}_af", []).append((waarde, False))
            elif sleutel == "stat_rate_to":
                vermogen.setdefault(f"{soort}_terug", []).append((waarde, False))

    for bron in (voorkeuren or {}).get("energy_sources") or []:
        if isinstance(bron, dict) and (soort := {"grid": "net", "solar": "zon", "battery": "batterij"}.get(bron.get("type"))):
            loop(bron, soort)
    return vermogen, energie


def zoek_sensoren(entiteiten: list[Entiteit], voorkeuren: dict | None = None) -> Sensoren:
    uit = Sensoren()
    per_id = {e.entity_id: e for e in entiteiten}
    vermogen, energie = uit_energiedashboard(voorkeuren)
    apparaten = {soort: {per_id[i].apparaat for i in ids if i in per_id and per_id[i].apparaat} for soort, ids in energie.items()}
    stroom = [e for e in entiteiten if e.is_vermogen and e.platform != "zonneburen"]

    # 1. Het Energie-dashboard.
    if vermogen.get("net"):
        uit.net, uit.net_om = vermogen["net"][0]
        uit.bron["net"] = "dashboard"
    elif vermogen.get("net_af") and vermogen.get("net_terug"):
        uit.net, uit.net_terug = vermogen["net_af"][0][0], vermogen["net_terug"][0][0]
        uit.bron["net"] = "dashboard"
    if vermogen.get("zon"):
        uit.zon = vermogen["zon"][0][0]
        uit.bron["zon"] = "dashboard"
    if vermogen.get("batterij"):
        uit.batterij, uit.batterij_om = vermogen["batterij"][0]
        uit.bron["batterij"] = "dashboard"

    # 2. Bekende slimme meters.
    if not uit.net:
        for e in stroom:
            if e.platform not in METERS or (e.platform == "homewizard" and not _is_p1(e, entiteiten)):
                continue
            af, terug = METERS[e.platform]
            if _heeft(e.tekst, *af) and not _heeft(e.tekst, *NIET_NU, " l1 ", " l2 ", " l3 ", " phase "):
                uit.net = e.entity_id
                if terug:
                    uit.net_terug = next((t.entity_id for t in stroom if t.platform == e.platform and t.apparaat == e.apparaat
                                          and _heeft(t.tekst, *terug) and not _heeft(t.tekst, *NIET_NU, " l1 ", " l2 ", " l3 ")), None)
                    if not uit.net_terug:
                        uit.net = None
                        continue
                uit.bron["net"] = "merk"
                break

    # 3. Trefwoorden, het liefst op het apparaat van de netmeter uit het dashboard.
    if not uit.net:
        uit.net = _beste(stroom, NET_WOORDEN, apparaten.get("net", set()),
                         verboden=(*ZON_WOORDEN, *BAT_WOORDEN, " export ", " return ", " teruglever", " l1 ", " l2 ", " l3 ", " phase "))
        if uit.net:
            uit.bron["net"] = "naam"
    if not uit.zon:
        uit.zon = _beste(stroom, ZON_WOORDEN, apparaten.get("zon", set()),
                         verboden=(*BAT_WOORDEN, " grid ", " export ", " import ", " consumption ", " verbruik ", " reactive ",
                                   " meter ", " l1 ", " l2 ", " l3 ", " phase ", " string ", " pv1 ", " pv2 ", " mppt "),
                         uitsluiten={uit.net, uit.net_terug},
                         extra=lambda e: 15 if e.platform in ZON_MERKEN and _heeft(e.tekst, " ac power ", " active power ",
                                                                                   " input power ", " power ") else 0)
        if uit.zon:
            uit.bron["zon"] = "naam"
    if not uit.batterij:
        kandidaat = _beste(stroom, BAT_WOORDEN, apparaten.get("batterij", set()),
                           verboden=(" grid ", " pv ", " solar ", " voltage ", " current ", " temperature "),
                           uitsluiten={uit.net, uit.net_terug, uit.zon})
        if kandidaat:
            uit.batterij = kandidaat
            uit.batterij_om = per_id[kandidaat].platform in BATTERIJ_LADEN_POSITIEF
            uit.bron["batterij"] = "naam"

    # Het laadniveau hoort bij de batterij; nooit dat van een telefoon of een draadloze sensor.
    bat_apparaat = per_id[uit.batterij].apparaat if uit.batterij and uit.batterij in per_id else None
    beste, score = None, 0
    for e in entiteiten:
        if e.domein != "sensor" or e.eenheid != "%" or e.platform in ("mobile_app", "zonneburen"):
            continue
        punten = 0
        if bat_apparaat and e.apparaat == bat_apparaat and (e.device_class == "battery" or _heeft(e.tekst, " soc ", " state of charge ")):
            punten += 50
        if _heeft(e.tekst, " soc ", " state of charge ", " laadniveau ", " battery level "):
            punten += 30
        if e.apparaat in apparaten.get("batterij", set()):
            punten += 20
        if punten >= 30 and punten > score:
            beste, score = e.entity_id, punten
    uit.soc = beste
    if beste:
        uit.bron["soc"] = "naam"

    uit.verbruik = _beste(stroom, VERBRUIK_WOORDEN, set(), verboden=(" grid ", " net ", " p1 ", " battery ", " pv "),
                          uitsluiten={uit.net, uit.net_terug, uit.zon, uit.batterij}, drempel=20)
    if uit.verbruik:
        uit.bron["verbruik"] = "naam"
    return uit


def _is_p1(e: Entiteit, entiteiten: list[Entiteit]) -> bool:
    """HomeWizard maakt ook stekkers en kWh-meters; alleen de P1-meter is het net."""
    if _heeft(e.apparaattekst, " p1 ", " hwe p1 "):
        return True
    return any(a.apparaat == e.apparaat and _heeft(a.tekst, " active tariff ", " smr version ") for a in entiteiten if a.apparaat)


def _beste(kandidaten: list[Entiteit], woorden: tuple[str, ...], apparaten: set, verboden: tuple[str, ...] = (),
           uitsluiten: set | None = None, drempel: int = 20, extra=None) -> str | None:
    beste, score = None, 0
    for e in kandidaten:
        if uitsluiten and e.entity_id in uitsluiten:
            continue
        tekst = e.tekst
        if _heeft(tekst, *NIET_NU, *verboden):
            continue
        punten = 20 * sum(woord in tekst for woord in woorden)
        if e.apparaat and e.apparaat in apparaten:
            punten += 25
        if extra:
            punten += extra(e)
        if punten >= drempel and punten > score:
            beste, score = e.entity_id, punten
    return beste


# ---------------------------------------------------------------------------------------------- Batterij sturen

@dataclass
class Sturing:
    """Hoe de batterij het energieplan volgt. Drie soorten:

    - vermogen: één getal, positief is ontladen (of laden, met laden_positief);
    - netdoel: één getal voor het gewenste vermogen op het net, zoals bij een Victron ESS;
    - apart: een getal om te laden, een getal om te ontladen en een keuze voor de richting.
    """
    soort: str = ""
    vermogen: str | None = None
    ontlaad: str | None = None
    laden_positief: bool = False
    modus: str | None = None
    modus_sturen: str | None = None
    modus_terug: str | None = None
    richting: str | None = None
    richting_laden: str | None = None
    richting_ontladen: str | None = None
    richting_stop: str | None = None
    merk: str = ""


# Per soort optie de woorden, de beste eerst: "Maximize Self Consumption" wint van "Disabled".
OPTIE_WOORDEN = {
    "ontladen": (" discharge ", " discharging ", " ontladen ", " ontlaad"),
    "laden": (" force charge ", " forcible charge ", " charge from solar power and grid ", " grid charge ", " charge ",
              " charging ", " laden "),
    "stop": (" stop ", " standby ", " stand by ", " idle ", " hold ", " pause ", " no command ", " none ",
             " solar power only ", " off ", " uit "),
    "terug": (" self consumption ", " self use ", " zelfverbruik ", " maximi", " eigen ", " general ", " auto", " normal ",
              " smart ", " default ", " disable"),
    "sturen": (" remote ", " extern", " rs485 ", " modbus ", " vpp ", " manual ", " handmatig ", " command ", " api ",
               " third party ", " passive ", " forced ", " force ", " enable", " on "),
}
SETPOINT_WOORDEN = (" grid setpoint ", " grid set point ", " ess grid ", " grid target ", " netdoel ", " ac power setpoint ")
ONTLADEN_WOORDEN = OPTIE_WOORDEN["ontladen"]


def _optie_rang(optie: str) -> tuple[str, int]:
    """De soort van een optie (laden, ontladen, stop, terug, sturen of leeg) en hoe goed hij past."""
    tekst = _plat(optie)
    for soort, woorden in OPTIE_WOORDEN.items():
        for rang, woord in enumerate(woorden):
            if woord in tekst:
                return soort, rang
    return "", 0


def soort_optie(optie: str) -> str:
    return _optie_rang(optie)[0]


def kies_opties(opties: list[str]) -> dict[str, str]:
    """Per soort de optie die het best past."""
    beste: dict[str, tuple[int, str]] = {}
    for optie in opties:
        soort, rang = _optie_rang(optie)
        if soort and (soort not in beste or rang < beste[soort][0]):
            beste[soort] = (rang, optie)
    return {soort: optie for soort, (_, optie) in beste.items()}


# Getallen die bij een omvormer horen maar de batterij niet sturen. De teruglevergrens van een Deye
# (zero_export_power, 0-500 W) leek op een batterijvermogen: die werd op 0 gezet en de batterij nooit gestuurd.
GEEN_BATTERIJVERMOGEN = (" zero export ", " export limit ", " peak shaving ", " max export ", " max import ", " pv power ",
                         " generator ", " smartload ", " smart load ")
MIN_BEREIK_W = 1000


def geschikt_vermogen(e: Entiteit) -> bool:
    """Kan dit getal een batterij sturen? In W of kW, met een bereik van minstens 1000 W, en geen grens of generator."""
    if e.eenheid not in ("W", "kW") or _heeft(e.tekst, *GEEN_BATTERIJVERMOGEN):
        return False
    factor = 1000 if e.eenheid == "kW" else 1
    bereik = max(abs(e.maximum or 0), abs(e.minimum or 0)) * factor
    return bereik >= MIN_BEREIK_W


@dataclass
class DeyeTou:
    """De tijdprogramma's van een Deye of Sunsynk (Solarman): zes programma's met een laden-keuze, een lading en
    een tijd, de laadstroom van het net en de accuspanning."""
    tou: str
    laden: list[str]
    soc: list[str]
    tijd: list[str]
    stroom: str | None = None
    spanning: str | None = None
    min_soc: str | None = None
    merk: str = ""


def _programma(entity_id: str, deel: str) -> int | None:
    """Het nummer van het programma uit bijvoorbeeld select.deye_program_3_charging, of None."""
    naam = entity_id.split(".", 1)[-1]
    for voor in ("program_", "prog", "time_of_use_"):
        i = naam.find(voor)
        if i >= 0:
            rest = naam[i + len(voor):]
            cijfers = rest[:1]
            if cijfers.isdigit() and rest[1:].lstrip("_").startswith(deel):
                return int(cijfers)
    return None


def zoek_deye(entiteiten: list[Entiteit]) -> DeyeTou | None:
    """Een Deye of Sunsynk herken je aan time_of_use plus program_1_charging op hetzelfde apparaat."""
    keuzes = [e for e in entiteiten if e.domein == "select" and e.platform != "zonneburen"]
    for tou in (e for e in keuzes if e.entity_id.endswith("time_of_use") or e.entity_id.endswith("_tou")):
        zelfde = [e for e in entiteiten if e.platform != "zonneburen" and (e.apparaat == tou.apparaat if tou.apparaat else True)]

        def per_nummer(domein: str, deel: str, zelfde: list[Entiteit] = zelfde) -> list[str]:
            gevonden = {}
            for e in zelfde:
                if e.domein == domein and (n := _programma(e.entity_id, deel)) is not None and 1 <= n <= 6:
                    gevonden.setdefault(n, e.entity_id)
            return [gevonden[n] for n in sorted(gevonden)]

        laden, soc = per_nummer("select", "charg"), per_nummer("number", "soc")
        if not laden or not soc or not any(_programma(i, "charg") == 1 for i in laden):
            continue

        def een(domein: str, *eindes: str, zelfde: list[Entiteit] = zelfde) -> str | None:
            return next((e.entity_id for e in zelfde if e.domein == domein and any(e.entity_id.endswith(x) for x in eindes)), None)

        return DeyeTou(
            tou=tou.entity_id, laden=laden, soc=soc, tijd=per_nummer("time", "time"),
            stroom=een("number", "battery_grid_charging_current", "grid_charging_current", "grid_charge_current"),
            spanning=een("sensor", "battery_voltage"),
            min_soc=een("number", "battery_low_soc", "battery_shutdown_soc"),
            merk=tou.merk or tou.platform)
    return None


def zoek_sturing(entiteiten: list[Entiteit], sensoren: Sensoren | None = None) -> Sturing | None:
    """Zoekt bij de batterij de getallen en keuzes waarmee Zonneburen hem kan sturen."""
    per_id = {e.entity_id: e for e in entiteiten}
    thuis = {per_id[i].apparaat for i in (sensoren.batterij, sensoren.soc) if sensoren and i and i in per_id and per_id[i].apparaat} \
        if sensoren else set()
    platforms = {per_id[i].platform for i in (sensoren.batterij, sensoren.soc) if sensoren and i and i in per_id} if sensoren else set()

    def dichtbij(e: Entiteit) -> int:
        return 30 if e.apparaat and e.apparaat in thuis else 15 if e.platform in platforms else 0

    # Alleen getallen die echt een batterij kunnen sturen. Bij twijfel kiezen we niets en kiest de bewoner zelf.
    getallen = [e for e in entiteiten if e.domein in ("number", "input_number") and e.platform != "zonneburen" and geschikt_vermogen(e)]
    keuzes = [e for e in entiteiten if e.domein in ("select", "input_select") and e.opties and e.platform != "zonneburen"]
    schakelaars = [e for e in entiteiten if e.domein == "switch" and e.platform != "zonneburen"]

    def batterijachtig(e: Entiteit) -> bool:
        return dichtbij(e) > 0 or _heeft(e.tekst, *BAT_WOORDEN, " ess ", " charge ", " discharge ")

    # Netdoel (Victron ESS): het sterkste signaal.
    for e in sorted(getallen, key=dichtbij, reverse=True):
        if _heeft(e.tekst, *SETPOINT_WOORDEN):
            return _met_modus(Sturing(soort="netdoel", vermogen=e.entity_id, merk=e.merk or e.platform), keuzes, schakelaars, dichtbij)

    # Aparte getallen om te laden en te ontladen, met een keuze voor de richting.
    laad = _beste_getal(getallen, dichtbij, lambda t: _heeft(t, " charge ", " charging ", " laad ", " laden ")
                        and not _heeft(t, *ONTLADEN_WOORDEN), batterijachtig)
    ontlaad = _beste_getal(getallen, dichtbij, lambda t: _heeft(t, *ONTLADEN_WOORDEN), batterijachtig)
    if laad and ontlaad and laad != ontlaad and not (per_id[laad].minimum or 0) < 0:
        sturing = Sturing(soort="apart", vermogen=laad, ontlaad=ontlaad, merk=per_id[laad].merk or per_id[laad].platform)
        richting = max((k for k in keuzes if {"laden", "ontladen"} <= set(kies_opties(k.opties))),
                       key=lambda k: dichtbij(k) + 10 * _heeft(k.tekst, " force ", " forcible ", " command ", " mode "), default=None)
        if richting:
            gekozen = kies_opties(richting.opties)
            sturing.richting = richting.entity_id
            sturing.richting_laden, sturing.richting_ontladen = gekozen["laden"], gekozen["ontladen"]
            sturing.richting_stop = gekozen.get("stop")
        return _met_modus(sturing, keuzes, schakelaars, dichtbij, overslaan=sturing.richting)

    # Eén getal met een teken: negatief laden, positief ontladen (of andersom).
    signed = [e for e in getallen if (e.minimum or 0) < 0 < (e.maximum or 0) and batterijachtig(e)]
    if signed:
        e = max(signed, key=lambda g: dichtbij(g) + 10 * _heeft(g.tekst, " setpoint ", " target ", " power ", " vermogen "))
        tekst = e.tekst
        if _heeft(tekst, " charge discharge ", " charge and discharge "):
            laden_positief = True
        elif _heeft(tekst, *ONTLADEN_WOORDEN):
            laden_positief = False
        else:
            laden_positief = _heeft(tekst, " charge ", " charging ", " laad ", " laden ")
        return _met_modus(Sturing(soort="vermogen", vermogen=e.entity_id, laden_positief=laden_positief, merk=e.merk or e.platform),
                          keuzes, schakelaars, dichtbij)
    return None


def _beste_getal(getallen, dichtbij, past, batterijachtig) -> str | None:
    kandidaten = [e for e in getallen if past(e.tekst) and batterijachtig(e) and not _heeft(e.tekst, " cutoff ", " soc ", " current ")]
    if not kandidaten:
        return None

    def punten(e: Entiteit) -> int:
        return dichtbij(e) + 10 * _heeft(e.tekst, " force ", " forcible ", " remote ", " setpoint ", " target ", " command ") \
            - 5 * _heeft(e.tekst, " limit ", " max ")
    return max(kandidaten, key=punten).entity_id


def _met_modus(sturing: Sturing, keuzes, schakelaars, dichtbij, overslaan: str | None = None) -> Sturing:
    """De stand voor externe sturing: een keuze met een sturen- en een terug-optie, of een schakelaar."""
    beste, score = None, 0
    for k in keuzes:
        if k.entity_id == overslaan:
            continue
        gekozen = kies_opties(k.opties)
        if "sturen" in gekozen and "terug" in gekozen:
            punten = dichtbij(k) + 10 + 10 * _heeft(k.tekst, " control ", " mode ", " ems ", " work mode ", " stand ")
            if punten > score and (dichtbij(k) or _heeft(k.tekst, *BAT_WOORDEN, " ess ", " inverter ", " storage ")):
                beste, score = k, punten
    if beste:
        gekozen = kies_opties(beste.opties)
        sturing.modus, sturing.modus_sturen, sturing.modus_terug = beste.entity_id, gekozen["sturen"], gekozen["terug"]
        return sturing
    for s in schakelaars:
        if dichtbij(s) and _heeft(s.tekst, " rs485 ", " remote ", " control mode ", " extern", " modbus control "):
            sturing.modus = s.entity_id
            return sturing
    return sturing


# ---------------------------------------------------------------------------------------------- Apparaten

WARMTEPOMP_WOORDEN = (" warmtepomp ", " heat pump ", " heatpump ", " quatt ", " nibe ", " aquarea ", " heishamon ", " altherma ",
                      " ecodan ", " weheat ", " luxtronik ", " alpha innotec ", " mypyllant ", " arotherm ", " vaillant ",
                      " ochsner ", " vicare ", " viessmann ", " stiebel ", " itho ", " intergas ", " remeha ", " elga ",
                      " atlantic ", " therma v ", " ehs ", " daikin onecta ", " panasonic ", " lg thinq ")
LAADPAAL_WOORDEN = (" laadpaal ", " laadpunt ", " wallbox ", " charger ", " charge point ", " chargepoint ", " easee ", " zaptec ",
                    " alfen ", " ohme ", " peblar ", " go e ", " goecharger ", " zappi ", " myenergi ", " evbox ", " wall connector ",
                    " pulsar ", " evse ", " ocpp ")
BOILER_WOORDEN = (" boiler ", " close in ", " keukenboiler ", " warm water ", " hot water ", " tapwater ", " dhw ", " water heater ")
LAAD_SCHAKELAAR = (" charging ", " charge ", " laden ", " enable ", " start ", " charger ")
NIET_SCHAKELEN = (" lock ", " cable ", " led ", " light ", " auto update ", " firmware ", " phase ", " restart ", " reboot ", " schedule ")


@dataclass
class ApparaatVoorstel:
    entity_id: str
    naam: str
    rol: int
    soort: str            # Warmtepomp, Boiler, Laadpaal of Airco: om te tonen
    vermogen: str = ""
    watts: int = 2000
    normaal_aan: bool = False
    normaal_temp: int = 0
    boost_temp: int = 0

    def als_apparaat(self) -> dict:
        """In de vorm van de apparatenlijst van de hub."""
        return {"enabled": True, "name": self.naam[:64], "role": self.rol, "type": 6, "host": self.entity_id,
                "powerEntity": self.vermogen, "boostTemp": self.boost_temp, "normalTemp": self.normaal_temp,
                "watts": self.watts, "cascade": True, "wijk": True, "normalOn": self.normaal_aan}


def zoek_apparaten(entiteiten: list[Entiteit], al_gekozen: set[str] | None = None) -> list[ApparaatVoorstel]:
    """Warmtepompen, boilers, laadpalen en airco's die Zonneburen kan schakelen.

    'Normaal' is zoals het nu staat: zo verandert er niets tot de wijk stroom over heeft."""
    al_gekozen = al_gekozen or set()
    uit: list[ApparaatVoorstel] = []
    gehad: set[tuple] = set()

    def vermogen_van(e: Entiteit) -> str:
        if not e.apparaat:
            return ""
        kandidaten = [s for s in entiteiten if s.apparaat == e.apparaat and s.is_vermogen and not _heeft(s.tekst, *NIET_NU)]
        kandidaten.sort(key=lambda s: -_heeft(s.tekst, " power ", " vermogen ", " consumption "))
        return kandidaten[0].entity_id if kandidaten else ""

    def naam_van(e: Entiteit) -> str:
        return e.apparaatnaam or e.naam or e.entity_id

    for e in entiteiten:
        if e.entity_id in al_gekozen or e.platform == "zonneburen":
            continue
        tekst, bij = e.tekst, e.apparaattekst
        voorstel = None
        if e.domein == "water_heater":
            doel = round(e.doeltemp or 0)
            voorstel = ApparaatVoorstel(e.entity_id, naam_van(e), ROL_BOILER, "Boiler", watts=2000, normaal_aan=e.staat not in ("off", None),
                                        normaal_temp=doel, boost_temp=min(int(e.max_temp or 65), doel + 10) if doel else 0)
        elif e.domein == "climate" and _heeft(tekst + bij, *WARMTEPOMP_WOORDEN):
            doel = round(e.doeltemp or 0)
            voorstel = ApparaatVoorstel(e.entity_id, naam_van(e), ROL_ANDERS, "Warmtepomp", watts=1500, normaal_aan=e.staat not in ("off", None),
                                        normaal_temp=doel, boost_temp=min(int(e.max_temp or 30), doel + 1) if doel else 0)
        elif e.domein == "climate" and "cool" in e.hvac_modi and not _heeft(tekst + bij, " thermostat ", " trv ", " radiator "):
            voorstel = ApparaatVoorstel(e.entity_id, naam_van(e), ROL_AIRCO, "Airco", watts=1000, normaal_aan=e.staat not in ("off", None))
        elif e.domein == "switch" and _heeft(bij + tekst, *LAADPAAL_WOORDEN) and _heeft(tekst, *LAAD_SCHAKELAAR) \
                and not _heeft(tekst, *NIET_SCHAKELEN):
            voorstel = ApparaatVoorstel(e.entity_id, naam_van(e), ROL_LAADPAAL, "Laadpaal", watts=11000, normaal_aan=e.staat == "on")
        elif e.domein == "switch" and _heeft(tekst + bij, *BOILER_WOORDEN) and not _heeft(tekst, *NIET_SCHAKELEN):
            voorstel = ApparaatVoorstel(e.entity_id, naam_van(e), ROL_BOILER, "Boiler", watts=2000, normaal_aan=e.staat == "on")
        if voorstel and (e.apparaat, voorstel.soort) in gehad:
            continue
        if voorstel:
            voorstel.vermogen = vermogen_van(e)
            uit.append(voorstel)
            if e.apparaat:
                gehad.add((e.apparaat, voorstel.soort))
    volgorde = {"Warmtepomp": 0, "Boiler": 1, "Laadpaal": 2, "Airco": 3}
    uit.sort(key=lambda v: (volgorde[v.soort], v.naam.lower()))
    return uit[:12]
