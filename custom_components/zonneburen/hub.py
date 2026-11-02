"""Home Assistant als Zonneburen-hub.

Doet voor de webapp en de wijk-server hetzelfde als de Zonneburen Link: telemetrie per minuut,
live gegevens zolang iemand kijkt, opdrachten uit de webapp en de wijkcommando's. Omdat alles al in
Home Assistant staat, kan dit zonder omwegen: de apparatenlijst is direct klaar en elk apparaat dat
Home Assistant kent, doet mee.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import protocol
from .const import (
    CONF_ADRES, CONF_APPARAAT_ID, CONF_APPARATEN, CONF_BATTERIJ, CONF_BATTERIJ_OM, CONF_BEDIENING, CONF_NAAM,
    CONF_NET, CONF_NET_OM, CONF_OVERIG, CONF_SOC, CONF_VERBRUIK, CONF_WIJK_AUTOMATISCH, CONF_ZON, DOMAIN, FIRMWARE,
    SIGNAAL_BIJGEWERKT,
)
from .verbinding import Verbinding

_LOGGER = logging.getLogger(__name__)

TIK = timedelta(seconds=5)
MINUUT = timedelta(seconds=60)
BEWAREN = timedelta(minutes=10)
KIJKEN_S = 120
LIVE_GEKEKEN_S = 5
LIVE_RUSTIG_S = 60
INFO_S = 300
VAK_S = 300                      # detail voor de grafiek in Inzichten
MAX_VAKKEN = 48 * 3600 // VAK_S  # twee dagen, zoals de hub
NET_WEG_S = 300
ANDERE_HUB_S = 3600   # niet elke minuut opnieuw melden

INFO, GOED, LET_OP, ALARM = 0, 1, 2, 3
STANDEN = {"on": 1, "auto": 0, "off": -1}
UIT = (STATE_UNAVAILABLE, STATE_UNKNOWN, "off", "idle")

# Instellingen van de hub die hier geen betekenis hebben: de bron is Home Assistant zelf.
ALLEEN_OP_DE_HUB = (
    "smHost", "smPort", "smSerial", "cloudAppId", "cloudSecret", "cloudEmail", "cloudPassword", "cloudStation",
    "battHost", "battPort", "battUnit", "battToken", "battUser", "battPass", "meterHost", "meterPort", "meterInvert",
    "slaveId", "profile", "hcClientId",
)
NAAR_OPTIE = {
    "haEntGrid": CONF_NET, "haEntPv": CONF_ZON, "haEntBat": CONF_BATTERIJ, "haEntSoc": CONF_SOC, "haEntLoad": CONF_VERBRUIK,
    "invertGrid": CONF_NET_OM, "invertBattery": CONF_BATTERIJ_OM, "deviceName": CONF_NAAM,
}


class ZonneburenHub:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.adres: str = entry.data[CONF_ADRES]
        self.hub_id: str = entry.data[CONF_APPARAAT_ID]
        self.verbinding = Verbinding(hass, dict(entry.data), self._bij_bericht, self._bij_verbinding)
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")

        self.minuut = protocol.MinuutGemiddelde()
        self.dagen = protocol.Dagtotalen()
        self.vakken: deque[dict] = deque(maxlen=MAX_VAKKEN)
        self._vak: dict[str, list[float]] = {}
        self._vak_start = 0.0
        self.logboek: deque[dict] = deque(maxlen=150)

        self.live: dict = {}
        self.wijk_actief = False
        self.noodstop = False
        self.vraag = False
        self.prijzen: dict | None = None
        self.wijk_overschot: float | None = None
        self.week: dict | None = None
        self.standen = [0] * protocol.MAX_APPARATEN

        self._gestart = time.time()
        self._kijken_tot = 0.0
        self._volgende_live = 0.0
        self._volgende_info = 0.0
        self._net_weg_sinds: float | None = None
        self._net_weg_gemeld = False
        self.andere_hub: str | None = None
        self._andere_hub_op = 0.0

    # ------------------------------------------------------------------ Levensloop

    async def async_start(self) -> None:
        if opgeslagen := await self._store.async_load():
            self.dagen.dagen = opgeslagen.get("dagen", {})
            self.vakken.extend(opgeslagen.get("vakken", []))
            self.logboek.extend(opgeslagen.get("logboek", []))
            self.week = opgeslagen.get("week")
            standen = opgeslagen.get("standen", [])
            self.standen = [int(s) if s in (-1, 0, 1) else 0 for s in standen][: protocol.MAX_APPARATEN]
            self.standen += [0] * (protocol.MAX_APPARATEN - len(self.standen))

        self._log(INFO, f"Gestart als hub ({FIRMWARE})")
        self.verbinding.start()
        for interval, actie in ((TIK, self._tik), (MINUUT, self._stuur_telemetrie), (BEWAREN, self._bewaar)):
            self.entry.async_on_unload(async_track_time_interval(self.hass, actie, interval))

    async def async_stop(self) -> None:
        await self._async_bewaar()
        await self.verbinding.async_stop()

    @callback
    def _bewaar(self, _nu=None) -> None:
        self.hass.async_create_task(self._async_bewaar())

    async def _async_bewaar(self) -> None:
        await self._store.async_save({
            "dagen": self.dagen.dagen, "vakken": list(self.vakken), "logboek": list(self.logboek),
            "week": self.week, "standen": self.standen,
        })

    @property
    def opties(self) -> dict:
        return self.entry.options

    def _apparaten(self) -> list[dict]:
        return protocol.normaliseer_apparaten(self.opties.get(CONF_APPARATEN))

    @callback
    def _bijgewerkt(self) -> None:
        async_dispatcher_send(self.hass, f"{SIGNAAL_BIJGEWERKT}_{self.entry.entry_id}")

    # ------------------------------------------------------------------ Meten

    def _getal(self, entity_id: str | None) -> tuple[float | None, str | None]:
        if not entity_id or not (staat := self.hass.states.get(entity_id)) or staat.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None, None
        try:
            return float(staat.state), staat.attributes.get("unit_of_measurement")
        except ValueError:
            return None, None

    def _watt(self, sleutel: str, omdraaien: bool = False) -> float | None:
        waarde, eenheid = self._getal(self.opties.get(sleutel))
        if waarde is None:
            return None
        if eenheid == "kW":
            waarde *= 1000
        return -waarde if omdraaien else waarde

    @callback
    def _tik(self, _nu=None) -> None:
        nu = time.time()
        net = self._watt(CONF_NET, self.opties.get(CONF_NET_OM, False))
        zon = self._watt(CONF_ZON)
        bat = self._watt(CONF_BATTERIJ, self.opties.get(CONF_BATTERIJ_OM, False))
        soc, _ = self._getal(self.opties.get(CONF_SOC))
        verbruik = self._watt(CONF_VERBRUIK)
        # Zonder eigen verbruikssensor volgt het verbruik uit de energiebalans, net als op de hub.
        if verbruik is None and net is not None:
            verbruik = max(net + (zon or 0) + (bat or 0), 0)

        if net is not None:
            self.minuut.voeg_toe(net, nu)
        self.dagen.voeg_toe(dt_util.now().date(), nu, zon, verbruik, net, bat)
        self._houd_vak_bij(nu, zon, verbruik, net, bat, soc)
        self._controleer_net(nu, net)

        vandaag = self.dagen.vandaag(dt_util.now().date())
        self.live = {k: v for k, v in {
            "valid": net is not None, "src": "ha", "ts": int(nu), "age": 0, "meter": False,
            "pv": _rond(zon), "grid": _rond(net), "bat": _rond(bat), "soc": _rond(soc), "load": _rond(verbruik),
            "wijk_net": _rond(self.wijk_overschot),
            "pv_today": round(vandaag["pv"], 1), "load_today": round(vandaag["load"], 1),
            "imp_today": round(vandaag["imp"], 1), "exp_today": round(vandaag["exp"], 1),
            "chg_today": round(vandaag["chg"], 1), "dis_today": round(vandaag["dis"], 1),
        }.items() if v is not None}

        if nu >= self._volgende_live:
            self._volgende_live = nu + (LIVE_GEKEKEN_S if nu < self._kijken_tot else LIVE_RUSTIG_S)
            self._publiceer_huis("hub/live", json.dumps(self.live))
        if nu >= self._volgende_info:
            self._volgende_info = nu + INFO_S
            self._publiceer_huis("hub/info", json.dumps(self._info(), ensure_ascii=False))

    def _houd_vak_bij(self, nu: float, zon, verbruik, net, bat, soc) -> None:
        if not self._vak_start:
            self._vak_start = nu
        for veld, waarde in (("pv", zon), ("load", verbruik), ("grid", net), ("bat", bat), ("soc", soc)):
            if waarde is not None:
                self._vak.setdefault(veld, []).append(waarde)
        if nu - self._vak_start < VAK_S:
            return
        gemiddeld = {veld: round(sum(w) / len(w)) for veld, w in self._vak.items() if w}
        self.vakken.append({"t": int(nu), **gemiddeld, "price": protocol.prijs_nu(self.prijzen, nu)})
        self._vak, self._vak_start = {}, nu

    def _controleer_net(self, nu: float, net: float | None) -> None:
        if net is not None:
            if self._net_weg_gemeld:
                self._log(GOED, "Net-sensor meet weer")
            self._net_weg_sinds, self._net_weg_gemeld = None, False
            return
        if self._net_weg_sinds is None:
            self._net_weg_sinds = nu
        elif not self._net_weg_gemeld and nu - self._net_weg_sinds > NET_WEG_S:
            self._net_weg_gemeld = True
            self._meld(LET_OP, "Net-sensor in Home Assistant geeft al 5 minuten geen waarde")

    @callback
    def _stuur_telemetrie(self, _nu=None) -> None:
        nu = time.time()
        if not (meting := self.minuut.neem(nu)):
            return
        soc, _ = self._getal(self.opties.get(CONF_SOC))
        bericht = protocol.bouw_telemetrie(self.adres, self.hub_id, FIRMWARE, meting, soc,
                                           protocol.prijs_nu(self.prijzen, nu), self._wijkvermogen(), False)
        self._publiceer_huis("telemetrie", json.dumps(bericht))

    # ------------------------------------------------------------------ Logboek en meldingen

    def _log(self, niveau: int, tekst: str) -> None:
        self.logboek.appendleft({"t": int(time.time()), "l": niveau, "m": tekst[:120]})
        _LOGGER.debug("%s", tekst)

    def _meld(self, niveau: int, tekst: str) -> None:
        """Komt in het logboek en, via de server, als mail of pushbericht bij de bewoner."""
        self._log(niveau, tekst)
        self._publiceer_huis("hub/melding", json.dumps({"niveau": niveau, "tijd": int(time.time()), "tekst": tekst[:120]}, ensure_ascii=False))

    def _publiceer_huis(self, blad: str, payload: str, qos: int = 0) -> None:
        self.verbinding.publiceer(protocol.huis_topic(self.adres, blad), payload, qos=qos)

    def _ack(self, commando: str, resultaat: str) -> None:
        self._publiceer_huis("ack", protocol.bouw_ack(self.adres, commando, resultaat, dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")))

    # ------------------------------------------------------------------ Berichten

    @callback
    def _bij_verbinding(self, verbonden: bool) -> None:
        self._log(GOED if verbonden else LET_OP, "Verbonden met Zonneburen" if verbonden else "Verbinding met Zonneburen verbroken")
        if verbonden:
            self._volgende_info = self._volgende_live = 0
        self._bijgewerkt()

    @callback
    def _bij_bericht(self, topic: str, payload: bytes) -> None:
        try:
            self._verwerk(topic, payload)
        except Exception:  # noqa: BLE001 - één vreemd bericht mag de hub niet stilleggen
            _LOGGER.exception("Bericht op %s niet verwerkt", topic)

    def _verwerk(self, topic: str, payload: bytes) -> None:
        if topic == protocol.huis_topic(self.adres, "hub/opdracht"):
            if opdracht := protocol.lees_opdracht(payload):
                self.hass.async_create_task(self._opdracht(opdracht))
        elif topic == protocol.huis_topic(self.adres, "telemetrie"):
            if andere := protocol.andere_hub(payload, self.adres, self.hub_id):
                self._zag_andere_hub(andere)
        elif topic == protocol.TOPIC_SCHAKELEN:
            if commando := protocol.lees_commando(payload, self.adres):
                self.hass.async_create_task(self._wijk_commando(*commando))
        elif topic == protocol.TOPIC_NOODSTOP:
            bericht = protocol.lees_json(payload) or {}
            self.hass.async_create_task(self._zet_noodstop(bool(bericht.get("actief"))))
        elif topic == protocol.TOPIC_WEEKRAPPORT:
            if week := protocol.lees_weekrapport(payload, self.adres):
                self.week = week
                self._log(GOED, f"Weekrapport: {week['gedeeld_kwh']:.1f} kWh gedeeld, plek {week['plek']} van {week['huizen']}")
                self._bijgewerkt()
        elif topic == protocol.TOPIC_WIJK_STATUS:
            bericht = protocol.lees_json(payload) or {}
            if isinstance(bericht.get("netto_overschot_watt"), (int, float)):
                self.wijk_overschot = bericht["netto_overschot_watt"]
                self._bijgewerkt()
        elif topic == protocol.TOPIC_PRIJZEN:
            self.prijzen = protocol.lees_json(payload)
            self._bijgewerkt()

    def _zag_andere_hub(self, hub_id: str) -> None:
        nu = time.time()
        self.andere_hub = hub_id
        if nu - self._andere_hub_op < ANDERE_HUB_S:
            return
        self._andere_hub_op = nu
        naam = "de Zonneburen Link" if not hub_id.startswith("ha-") and hub_id != "blueprint" else             "de Zonneburen-blueprint" if hub_id == "blueprint" else "een andere Home Assistant"
        tekst = f"Er stuurt nog een hub op dit adres: {naam}. De wijk gebruikt er maar één; zet de andere uit."
        self._log(ALARM, tekst)
        persistent_notification.async_create(self.hass, tekst, title="Zonneburen", notification_id=f"{DOMAIN}_andere_hub")
        self._bijgewerkt()

    async def _zet_noodstop(self, actief: bool) -> None:
        if actief != self.noodstop:
            self.noodstop = actief
            if actief:
                self._meld(ALARM, "Noodstop van het bestuur actief: apparaten terug op normaal")
            else:
                self._log(GOED, "Noodstop opgeheven")
            await self._pas_toe()
            self._bijgewerkt()
        if actief and self._heeft_wijkapparaten():
            self._ack("noodstop_broadcast", "uitgevoerd")

    def _heeft_wijkapparaten(self) -> bool:
        return any(a["enabled"] and a["wijk"] and a["type"] == protocol.SOORT_HOME_ASSISTANT and a["host"] for a in self._apparaten())

    async def _wijk_commando(self, commando: str, vraag: bool) -> None:
        start = commando in ("surplus_start", "stroom_overschot")
        stop = commando in ("surplus_stop", "normaal_bedrijf", "noodstop")
        if not start and not stop:
            return
        if not self._heeft_wijkapparaten():
            resultaat = "geen_stuurbare_apparaten"
        elif stop:
            self.wijk_actief = self.vraag = False
            await self._pas_toe()
            resultaat = "uitgevoerd"
        elif self.noodstop:
            resultaat = "geblokkeerd_piek_prijs_of_batterij"
        elif self.opties.get(CONF_WIJK_AUTOMATISCH, True):
            await self._start_wijk()
            resultaat = "uitgevoerd"
        elif vraag:
            # Vragen mag alleen als de server dat goedvindt: die doseert het aantal meldingen.
            self.vraag = True
            persistent_notification.async_create(
                self.hass, "De wijk heeft stroom over. Bevestig in het Zonneburen-dashboard om mee te doen.",
                title="Zonneburen", notification_id=f"{DOMAIN}_wijkvraag")
            resultaat = "wacht_op_bewoner"
        else:
            await self._start_wijk()
            resultaat = "uitgevoerd_via_vangnet"

        self._log(INFO, f"Wijk-commando {commando}: {resultaat}")
        self._ack(commando, resultaat)
        self._volgende_info = 0
        self._bijgewerkt()

    async def _start_wijk(self) -> None:
        self.wijk_actief, self.vraag = True, False
        persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_wijkvraag")
        await self._pas_toe()

    # ------------------------------------------------------------------ Apparaten

    def _gewenst(self, i: int, apparaat: dict) -> tuple[bool, str]:
        stand = self.standen[i]
        if stand == 1:
            return True, "Handmatig aan"
        if stand == -1:
            return False, "Handmatig uit"
        if self.noodstop:
            return apparaat["normalOn"], "Noodstop"
        if self.wijk_actief and apparaat["wijk"]:
            return True, "Wijkstroom"
        return apparaat["normalOn"], "Normaal aan" if apparaat["normalOn"] else "Uit"

    def _is_aan(self, entity_id: str) -> bool | None:
        staat = self.hass.states.get(entity_id)
        if not staat or staat.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return staat.state not in UIT

    async def _pas_toe(self) -> None:
        """Alleen bij een verandering, niet elke tik: wat de bewoner in Home Assistant zelf schakelt, blijft staan."""
        for i, apparaat in enumerate(self._apparaten()):
            if not apparaat["enabled"] or apparaat["type"] != protocol.SOORT_HOME_ASSISTANT or not apparaat["host"]:
                continue
            aan, _ = self._gewenst(i, apparaat)
            if self._is_aan(apparaat["host"]) is not aan or apparaat["host"].split(".", 1)[0] in ("climate", "water_heater"):
                await self._schakel(apparaat, aan)

    async def _schakel(self, apparaat: dict, aan: bool, dim: int | None = None) -> bool:
        entiteit = apparaat["host"]
        domein = entiteit.split(".", 1)[0]
        temperatuur = apparaat["boostTemp"] if aan else apparaat["normalTemp"]
        try:
            if domein in ("climate", "water_heater") and temperatuur:
                await self.hass.services.async_call(domein, "set_temperature", {"entity_id": entiteit, "temperature": temperatuur}, blocking=True)
            elif domein in ("number", "input_number") and temperatuur:
                await self.hass.services.async_call(domein, "set_value", {"entity_id": entiteit, "value": temperatuur}, blocking=True)
            elif domein == "light" and aan and dim is not None:
                await self.hass.services.async_call("light", "turn_on", {"entity_id": entiteit, "brightness_pct": dim}, blocking=True)
            else:
                await self.hass.services.async_call("homeassistant", "turn_on" if aan else "turn_off", {"entity_id": entiteit}, blocking=True)
        except Exception as fout:  # noqa: BLE001 - de service van een integratie kan van alles gooien
            self._log(LET_OP, f"{apparaat['name'] or entiteit}: schakelen mislukt ({fout})")
            return False
        return True

    def _vermogen(self, apparaat: dict) -> float | None:
        waarde, eenheid = self._getal(apparaat["powerEntity"])
        if waarde is not None:
            return waarde * 1000 if eenheid == "kW" else waarde
        return apparaat["watts"] if self._is_aan(apparaat["host"]) else None

    def _wijkvermogen(self) -> dict[int, int]:
        """Wat er nu op wijkstroom draait, per soort apparaat: zo verrekent de server het gedeelde deel."""
        per_rol: dict[int, int] = {}
        if not self.wijk_actief:
            return per_rol
        for i, apparaat in enumerate(self._apparaten()):
            if not apparaat["enabled"] or self._gewenst(i, apparaat)[1] != "Wijkstroom" or not self._is_aan(apparaat["host"]):
                continue
            per_rol[apparaat["role"]] = per_rol.get(apparaat["role"], 0) + round(self._vermogen(apparaat) or 0)
        return per_rol

    def _apparaten_json(self) -> dict:
        lijst = []
        for i, apparaat in enumerate(self._apparaten()):
            if not apparaat["enabled"]:
                continue
            staat = self.hass.states.get(apparaat["host"]) if apparaat["host"] else None
            _, reden = self._gewenst(i, apparaat)
            regel = {"i": i, "name": apparaat["name"], "role": apparaat["role"], "on": bool(self._is_aan(apparaat["host"])) if staat else False,
                     "mode": self.standen[i], "reason": reden, "ok": staat is not None and staat.state != STATE_UNAVAILABLE,
                     "watts": apparaat["watts"]}
            if apparaat["type"] != protocol.SOORT_HOME_ASSISTANT:
                regel.update(ok=False, error="Alleen apparaten uit Home Assistant")
            if (vermogen := self._vermogen(apparaat)) is not None:
                regel["power"] = round(vermogen)
            if staat and staat.domain == "light" and any(m != "onoff" for m in staat.attributes.get("supported_color_modes") or []):
                regel["dimbaar"] = True
                if (helderheid := staat.attributes.get("brightness")) is not None:
                    regel["dim"] = round(helderheid / 255 * 100)
            lijst.append(regel)
        return {"rules": True, "blocked": False, "wijkActive": self.wijk_actief, "emergency": self.noodstop,
                "question": self.vraag, "loads": lijst}

    def _info(self) -> dict:
        return {
            "fw": FIRMWARE, "id": self.hub_id, "naam": self.opties.get(CONF_NAAM) or "Home Assistant", "soort": "homeassistant",
            "uptime": int(time.time() - self._gestart), "bron": "ha", "meter": "none", "batterijOm": False,
            "bronFout": "Net-sensor geeft geen waarde" if self._net_weg_gemeld else "",
            "bediening": self.opties.get(CONF_BEDIENING, True), "regels": True, "vraag": self.vraag, "noodstop": self.noodstop,
            "meldingen": [], "apparaten": self._apparaten_json(), "witgoed": {"appliances": []},
        }

    def _ha_lijst(self) -> dict:
        entiteiten = []
        for staat in self.hass.states.async_all():
            eenheid = staat.attributes.get("unit_of_measurement")
            soort = protocol.entiteit_soort(staat.entity_id, eenheid)
            if soort and staat.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                entiteiten.append((soort, staat.name, {"e": staat.entity_id, "n": staat.name, "s": staat.state, "u": eenheid or ""}))
        entiteiten.sort(key=lambda e: (e[0], e[1].lower()))
        return {"entiteiten": [e[2] for e in entiteiten[:1500]]}

    # ------------------------------------------------------------------ Opdrachten uit de webapp

    async def _opdracht(self, opdracht: dict) -> None:
        actie = opdracht["actie"]
        fout: str | None = None
        data: dict | None = None
        if protocol.vraagt_bediening(actie) and not self.opties.get(CONF_BEDIENING, True):
            fout = "bediening op afstand staat uit in Home Assistant"
        elif actie == "kijk":
            self._kijken_tot = time.time() + KIJKEN_S
            self._volgende_live = self._volgende_info = 0
        elif actie == "gegevens":
            data, fout = self._gegevens(opdracht.get("soort"), opdracht.get("n"))
        elif actie == "zoek":
            data = {"apparaten": []}   # alles wat Home Assistant kent, staat al in de lijst van "gegevens ha"
        elif actie == "modus":
            fout = await self._zet_stand(opdracht.get("i"), opdracht.get("modus"))
        elif actie == "dim":
            fout = await self._dim(opdracht.get("i"), opdracht.get("procent"))
        elif actie == "wijkvraag":
            await self._beantwoord_wijkvraag(bool(opdracht.get("ja")))
        elif actie == "instellingen":
            fout = self._zet_instellingen(opdracht.get("waarden"))
        elif actie == "herstart":
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
        elif actie == "update":
            fout = "werk de integratie bij via HACS"
        elif actie in ("plan", "plan_stop"):
            fout = "plannen kan nog niet met Home Assistant"
        else:
            fout = "onbekende opdracht"

        self._publiceer_huis("hub/antwoord", protocol.bouw_antwoord(opdracht["id"], fout, data))
        if actie not in ("kijk", "gegevens", "zoek"):
            self._volgende_info = 0
            self._bijgewerkt()

    def _gegevens(self, soort, n) -> tuple[dict | None, str | None]:
        aantal = n if isinstance(n, int) else 24
        if soort == "instellingen":
            return self._instellingen(), None
        if soort == "logboek":
            return {"events": list(self.logboek)}, None
        if soort == "dagen":
            return self.dagen.als_json(max(1, min(aantal, 60))), None
        if soort == "uren":
            vanaf = time.time() - max(1, min(aantal, 48)) * 3600
            vakken = [v for v in self.vakken if v["t"] >= vanaf]
            return {veld: [v.get(veld) for v in vakken] for veld in ("t", "pv", "load", "grid", "bat", "soc", "price")}, None
        if soort == "ha":
            return self._ha_lijst(), None
        return None, "onbekend gegeven"

    def _instellingen(self) -> dict:
        o = self.opties
        return {
            **o.get(CONF_OVERIG, {}),
            "deviceName": o.get(CONF_NAAM, ""), "source": "ha", "meterType": "none", "pollSec": 5,
            "invertGrid": o.get(CONF_NET_OM, False), "invertBattery": o.get(CONF_BATTERIJ_OM, False),
            # Gemaskeerd: de webapp ziet Home Assistant als gekoppeld, maar stuurt het nooit terug.
            "haUrl": "Home Assistant", "haToken": "********",
            **{sleutel: o.get(optie) or "" for sleutel, optie in NAAR_OPTIE.items() if sleutel.startswith("haEnt")},
            "loads": self._apparaten(),
        }

    def _zet_instellingen(self, waarden) -> str | None:
        if not isinstance(waarden, dict):
            return "geen instellingen ontvangen"
        if any(sleutel in waarden for sleutel in ALLEEN_OP_DE_HUB):
            return "deze instelling hoort bij de Zonneburen Link, niet bij Home Assistant"
        if waarden.get("source", "ha") != "ha" or waarden.get("meterType", "none") != "none":
            return "kies je bronnen in de integratie in Home Assistant"

        opties = dict(self.opties)
        overig = dict(opties.get(CONF_OVERIG, {}))
        for sleutel, waarde in waarden.items():
            if sleutel in NAAR_OPTIE and sleutel.startswith("haEnt"):
                opties[NAAR_OPTIE[sleutel]] = waarde or None   # leeg betekent: geen sensor
            elif sleutel in NAAR_OPTIE:
                opties[NAAR_OPTIE[sleutel]] = waarde
            elif sleutel == "loads":
                opties[CONF_APPARATEN] = protocol.normaliseer_apparaten(waarde)
            elif sleutel not in ("source", "meterType", "haUrl", "haToken", "pollSec"):
                overig[sleutel] = waarde
        opties[CONF_OVERIG] = overig
        self.hass.config_entries.async_update_entry(self.entry, options=opties)
        self._log(INFO, "Via de webapp: instellingen aangepast")
        self.hass.async_create_task(self._pas_toe())
        return None

    async def _zet_stand(self, i, modus) -> str | None:
        if not isinstance(i, int) or not 0 <= i < protocol.MAX_APPARATEN or not self._apparaten()[i]["enabled"]:
            return "apparaat niet gevonden"
        if modus not in STANDEN:
            return "onbekende stand"
        self.standen[i] = STANDEN[modus]
        self._log(INFO, f"Via de webapp: {self._apparaten()[i]['name'] or i + 1} op {modus}")
        await self._pas_toe()
        return None

    async def _dim(self, i, procent) -> str | None:
        apparaten = self._apparaten()
        if not isinstance(i, int) or not 0 <= i < len(apparaten) or not apparaten[i]["host"].startswith("light."):
            return "dit apparaat kan niet dimmen"
        procent = max(1, min(int(procent if isinstance(procent, (int, float)) else 100), 100))
        self.standen[i] = 1
        return None if await self._schakel(apparaten[i], True, procent) else "dimmen mislukt"

    async def _beantwoord_wijkvraag(self, ja: bool) -> None:
        if not self.vraag:
            return
        if ja and not self.noodstop:
            await self._start_wijk()
            self._ack("surplus_start", "uitgevoerd")
        else:
            self.vraag = False
            persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_wijkvraag")
            self._ack("surplus_start", "geweigerd_door_bewoner")


def _rond(waarde: float | None) -> float | None:
    return None if waarde is None else round(waarde)
