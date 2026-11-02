"""Het Zonneburen-protocol: dezelfde berichten als de Zonneburen Link.

Bewust zonder Home Assistant, zodat dit los te testen is. De vorm volgt zb_protocol.cpp in de
firmware en de ZonneburenIncomingDto van de server; wijkt hij af, dan ziet de webapp de integratie
niet meer als hub.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date

TOPIC_SCHAKELEN = "zonneburen/schakelen"
TOPIC_NOODSTOP = "zonneburen/noodstop"
TOPIC_WEEKRAPPORT = "zonneburen/weekrapport"
TOPIC_WIJK_STATUS = "zonneburen/wijk/status"
TOPIC_PRIJZEN = "zonneburen/wijk/prijzen"
HUIS = "zonneburen/huis/"
STATUS = "zonneburen/status/"

# Een opdracht van de webapp is 2 minuten geldig, net als op de hub.
MAX_OPDRACHT_LEEFTIJD = 120
MAX_APPARATEN = 32

ROL_BOILER, ROL_LAADPAAL, ROL_AIRCO, ROL_ANDERS, ROL_LICHT = 0, 1, 2, 3, 4
SOORT_HOME_ASSISTANT = 6

STUURBARE_DOMEINEN = ("switch", "light", "input_boolean", "fan", "climate", "water_heater")


def huis_topic(adres: str, blad: str) -> str:
    return f"{HUIS}{adres}/{blad}"


def status_topic(adres: str) -> str:
    return f"{STATUS}{adres}"


def zelfde_adres(a: str | None, b: str | None) -> bool:
    """Hoofdletterongevoelig, spaties rond de waarde tellen niet (zoals zbTokenEquals)."""
    return a is not None and b is not None and a.strip().lower() == b.strip().lower()


def apparaat_id(instantie: str) -> str:
    """Vast per Home Assistant, en voor de server herkenbaar als software-hub."""
    return "ha-" + hashlib.sha256(instantie.encode()).hexdigest()[:16]


def bouw_telemetrie(adres: str, hub_id: str, versie: str, meting: dict, soc: float | None,
                    prijs: float | None, apparaten_w: dict[int, int], eigen_zon: bool) -> dict:
    boiler = apparaten_w.get(ROL_BOILER, 0)
    laadpaal = apparaten_w.get(ROL_LAADPAAL, 0)
    airco = apparaten_w.get(ROL_AIRCO, 0)
    overig = apparaten_w.get(ROL_ANDERS, 0) + apparaten_w.get(ROL_LICHT, 0)
    return {
        "blueprint_versie": versie,
        "hub_id": hub_id,
        "hardware_token": adres,
        "verbruik_watt": meting["afname"],
        "teruglevering_watt": meting["terug"],
        "netto_p1_watt": meting["afname"] - meting["terug"],
        "accu_soc": round(soc) if soc is not None else None,
        "beursprijs": round(prijs, 4) if prijs is not None else None,
        "p2p_meters": {
            "boiler_watt": boiler,
            "ev_laadpaal_watt": laadpaal,
            "airco_watt": airco,
            "overige_stekkers_watt": overig,
            "totaal_actief_p2p_watt": boiler + laadpaal + airco + overig,
        },
        "statussen": {"ev_ingeplugd": None, "eigen_zon_prioriteit": eigen_zon},
    }


def bouw_status(adres: str, online: bool) -> str:
    return json.dumps({"adres": adres, "online": online})


def bouw_ack(adres: str, commando: str, resultaat: str, tijdstip: str) -> str:
    return json.dumps({"adres": adres, "commando": commando, "resultaat": resultaat, "tijdstip": tijdstip})


def bouw_antwoord(opdracht_id: str, fout: str | None, data: dict | None) -> str:
    antwoord: dict = {"id": opdracht_id, "ok": fout is None}
    if fout:
        antwoord["fout"] = fout
    if data is not None:
        antwoord["data"] = data
    return json.dumps(antwoord, ensure_ascii=False)


def lees_json(payload: bytes | str) -> dict | None:
    try:
        waarde = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    return waarde if isinstance(waarde, dict) else None


def lees_commando(payload: bytes | str, adres: str) -> tuple[str, bool] | None:
    """Eén gedeeld topic voor de hele wijk: alleen berichten voor ons adres tellen."""
    bericht = lees_json(payload)
    if not bericht or not zelfde_adres(bericht.get("adres"), adres):
        return None
    commando = bericht.get("commando")
    if not isinstance(commando, str) or not commando:
        return None
    vraag = bericht.get("vraag_toestemming")
    return commando, vraag if isinstance(vraag, bool) else True


def andere_hub(payload: bytes | str, adres: str, eigen_hub_id: str) -> str | None:
    """Het hub_id van een andere zender op ons adres, of None. Zonder hub_id is het een oude blueprint."""
    bericht = lees_json(payload)
    if not bericht or not zelfde_adres(bericht.get("hardware_token"), adres):
        return None
    hub_id = bericht.get("hub_id")
    if hub_id == eigen_hub_id:
        return None
    return hub_id if isinstance(hub_id, str) and hub_id else "blueprint"


def lees_weekrapport(payload: bytes | str, adres: str) -> dict | None:
    bericht = lees_json(payload)
    if not bericht or not zelfde_adres(bericht.get("adres"), adres):
        return None
    return {
        "gedeeld_kwh": float(bericht.get("gedeeld_kwh") or 0),
        "plek": int(bericht.get("plek") or 0),
        "huizen": int(bericht.get("totaal_huizen") or 0),
        "bespaard_euro": float(bericht.get("wijk_bespaard_euro") or 0),
    }


def lees_opdracht(payload: bytes | str, nu: float | None = None) -> dict | None:
    """Zonder id, actie of verse tijd voert de hub niets uit: zo blijft niets ergens hangen."""
    opdracht = lees_json(payload)
    if not opdracht:
        return None
    ts = opdracht.get("ts")
    if not isinstance(opdracht.get("id"), str) or not isinstance(opdracht.get("actie"), str) or not isinstance(ts, (int, float)):
        return None
    if abs((nu if nu is not None else time.time()) - ts) > MAX_OPDRACHT_LEEFTIJD:
        return None
    return opdracht


def vraagt_bediening(actie: str) -> bool:
    return actie not in ("kijk", "gegevens", "zoek")


def prijs_nu(bericht: dict | None, nu: float) -> float | None:
    """zonneburen/wijk/prijzen: {t0, span, p: [...]} in EUR/kWh."""
    if not bericht:
        return None
    try:
        index = int((nu - bericht["t0"]) // bericht["span"])
        prijzen = bericht["p"]
    except (KeyError, TypeError, ZeroDivisionError):
        return None
    return float(prijzen[index]) if 0 <= index < len(prijzen) and prijzen[index] is not None else None


def entiteit_soort(entity_id: str, eenheid: str | None) -> int:
    """1 = stuurbaar, 2 = vermogens- of procentsensor, 0 = niet bruikbaar (zoals zbHaEntityKind)."""
    domein = entity_id.split(".", 1)[0]
    if domein in STUURBARE_DOMEINEN:
        return 1
    return 2 if domein == "sensor" and eenheid in ("W", "kW", "%") else 0


def leeg_apparaat() -> dict:
    return {
        "enabled": False, "name": "", "role": ROL_ANDERS, "type": 0, "host": "", "powerEntity": "",
        "boostTemp": 0, "normalTemp": 0, "watts": 2000, "cascade": True, "wijk": True, "normalOn": False,
    }


def normaliseer_apparaten(apparaten: list | None) -> list[dict]:
    """Altijd 32 plekken, zoals op de hub: de webapp zoekt de eerste lege plek."""
    uit = []
    for i in range(MAX_APPARATEN):
        bron = apparaten[i] if apparaten and i < len(apparaten) and isinstance(apparaten[i], dict) else {}
        apparaat = leeg_apparaat()
        for sleutel, standaard in apparaat.items():
            waarde = bron.get(sleutel, standaard)
            if isinstance(standaard, bool):
                apparaat[sleutel] = bool(waarde)
            elif isinstance(standaard, (int, float)) and not isinstance(waarde, bool) and isinstance(waarde, (int, float)):
                apparaat[sleutel] = type(standaard)(waarde)
            elif isinstance(standaard, str) and isinstance(waarde, str):
                apparaat[sleutel] = waarde[:64]
        uit.append(apparaat)
    return uit


@dataclass
class MinuutGemiddelde:
    """Net per minuut, zoals de hub: de server rekent elke rij als precies één minuut."""

    som_net: float = 0
    som_afname: float = 0
    som_terug: float = 0
    aantal: int = 0
    laatste: float | None = None
    laatste_op: float = 0

    def voeg_toe(self, net_w: float, nu: float) -> None:
        self.som_net += net_w
        self.som_afname += max(net_w, 0)
        self.som_terug += max(-net_w, 0)
        self.aantal += 1
        self.laatste, self.laatste_op = net_w, nu

    def neem(self, nu: float) -> dict | None:
        """Liever geen rij dan een valse nul; een meting tot 3 minuten oud mag nog mee."""
        if self.aantal:
            afname, terug = round(self.som_afname / self.aantal), round(self.som_terug / self.aantal)
        elif self.laatste is not None and nu - self.laatste_op < 180:
            afname, terug = round(max(self.laatste, 0)), round(max(-self.laatste, 0))
        else:
            return None
        self.som_net = self.som_afname = self.som_terug = 0
        self.aantal = 0
        return {"afname": afname, "terug": terug}


VELDEN = ("pv", "load", "imp", "exp", "chg", "dis")


@dataclass
class Dagtotalen:
    """kWh per dag uit vermogens, zoals de historie van de hub (400 dagen)."""

    dagen: dict[str, dict[str, float]] = field(default_factory=dict)
    vorige: float | None = None

    MAX_DAGEN = 400
    MAX_GAT = 120  # langer stil: dat stuk niet meetellen in plaats van gokken

    def voeg_toe(self, dag: date, nu: float, pv: float | None, load: float | None, net: float | None, bat: float | None) -> None:
        stap = 0 if self.vorige is None or nu - self.vorige > self.MAX_GAT else nu - self.vorige
        self.vorige = nu
        sleutel = dag.strftime("%Y%m%d")
        totaal = self.dagen.setdefault(sleutel, dict.fromkeys(VELDEN, 0.0))
        uur = stap / 3600
        for veld, watt in (("pv", pv), ("load", load), ("imp", net), ("exp", -net if net is not None else None),
                           ("chg", -bat if bat is not None else None), ("dis", bat)):
            if watt is not None and watt > 0:
                totaal[veld] += watt * uur / 1000
        while len(self.dagen) > self.MAX_DAGEN:
            del self.dagen[min(self.dagen)]

    def vandaag(self, dag: date) -> dict[str, float]:
        return self.dagen.get(dag.strftime("%Y%m%d"), dict.fromkeys(VELDEN, 0.0))

    def als_json(self, aantal: int) -> dict:
        return {"days": [
            {"date": int(sleutel), **{veld: round(waarde[veld], 1) for veld in VELDEN}, "gas": 0, "cost": 0, "rev": 0}
            for sleutel, waarde in sorted(self.dagen.items())[-aantal:]
        ]}
