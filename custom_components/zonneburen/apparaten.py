"""Apparaten veilig sturen: thermostaten en boilers via hun temperatuur, witgoed nooit midden in een programma.

Wat er per apparaat gebeurt, volgt uit wat het kan (domein, vermogensmeting, cloud of lokaal) en de soort
uit het energieplan; nooit uit een merknaam. De regels zelf staan in apparaat_regels.py.
"""

from __future__ import annotations

import logging
import time

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from . import apparaat_regels as r
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ONBEKEND = (STATE_UNAVAILABLE, STATE_UNKNOWN)
STANDAARD_DUUR_S = 2 * 3600


class ApparaatSturing:
    def __init__(self, hub) -> None:
        self.hub = hub
        self.hass = hub.hass
        self._store = Store(hub.hass, 1, f"{DOMAIN}.apparaten_{hub.entry.entry_id}")
        # Per entiteit: de gewenste temperatuur van de bewoner (de basis) en wat er voor Zonneburen stond.
        self.basis: dict[str, float] = {}
        self.origineel: dict[str, dict] = {}
        self.pauze_tot: dict[str, float] = {}
        self.uitgezet: set[str] = set()
        # Witgoed: klaargezet met "start slim", en het uiterlijke startmoment.
        self.klaargezet: dict[int, float | None] = {}
        self.klaar_om: dict[int, float] = {}
        self.loopt_door: set[int] = set()
        self.stil: dict[str, r.Stilstand] = {}
        self.limiet = r.Schrijflimiet()
        self._cloud: dict[str, bool] = {}
        self._besparen_sinds: dict[str, float] = {}
        self._vorige_wens: dict[str, str] = {}
        self._overgeslagen: set[str] = set()
        self._ratelimit_gemeld: set[str] = set()
        self._legionella_gemeld: set[str] = set()
        self.status: dict[int, str] = {}

    # ------------------------------------------------------------------ Opslaan

    async def laad(self) -> None:
        if data := await self._store.async_load():
            self.basis = {k: float(v) for k, v in (data.get("basis") or {}).items()}
            self.origineel = dict(data.get("origineel") or {})
            self.pauze_tot = {k: float(v) for k, v in (data.get("pauze_tot") or {}).items()}
            self.uitgezet = set(data.get("uitgezet") or [])
            self.klaargezet = {int(k): v for k, v in (data.get("klaargezet") or {}).items()}
            self.klaar_om = {int(k): float(v) for k, v in (data.get("klaar_om") or {}).items()}

    def bewaar(self) -> None:
        self._store.async_delay_save(lambda: {
            "basis": self.basis, "origineel": self.origineel, "pauze_tot": self.pauze_tot,
            "uitgezet": sorted(self.uitgezet), "klaargezet": {str(k): v for k, v in self.klaargezet.items()},
            "klaar_om": {str(k): v for k, v in self.klaar_om.items()},
        }, 5)

    # ------------------------------------------------------------------ Wat de entiteit kan

    async def is_cloud(self, entity_id: str) -> bool:
        """Praat de integratie via internet met het apparaat? Dan schrijven we veel zuiniger."""
        if entity_id not in self._cloud:
            cloud = False
            if (regel := er.async_get(self.hass).async_get(entity_id)) is not None:
                try:
                    integratie = await async_get_integration(self.hass, regel.platform)
                    cloud = str(integratie.manifest.get("iot_class") or "").startswith("cloud")
                except Exception:  # noqa: BLE001 - een onbekende integratie behandelen we als lokaal
                    cloud = False
            self._cloud[entity_id] = cloud
        return self._cloud[entity_id]

    def ratelimit_over(self, entity_id: str) -> float | None:
        """Hoeveel aanroepen de cloud vandaag nog toestaat, als de integratie dat meldt op hetzelfde apparaat."""
        reg = er.async_get(self.hass)
        regel = reg.async_get(entity_id)
        if regel is None or regel.device_id is None:
            return None
        for ander in er.async_entries_for_device(reg, regel.device_id):
            naam = ander.entity_id.lower()
            if ander.domain == "sensor" and ("ratelimit" in naam or "rate_limit" in naam) and "remaining" in naam \
                    and ("day" in naam or "dag" in naam):
                staat = self.hass.states.get(ander.entity_id)
                try:
                    return float(staat.state) if staat else None
                except ValueError:
                    return None
        return None

    async def _mag_schrijven(self, entity_id: str, waarde, nu: float, forceer: bool) -> bool:
        cloud = await self.is_cloud(entity_id)
        over = self.ratelimit_over(entity_id) if cloud else None
        if over is not None and over < r.RATELIMIT_MIN:
            if entity_id not in self._ratelimit_gemeld:
                self._ratelimit_gemeld.add(entity_id)
                self.hub._log(2, f"{entity_id}: de cloud staat vandaag nog maar {over:g} aanroepen toe; Zonneburen stuurt dit apparaat "
                                 "pas morgen weer")
            return forceer and over > 0
        self._ratelimit_gemeld.discard(entity_id)
        return forceer or self.limiet.mag(entity_id, waarde, nu, r.CLOUD_S if cloud else r.LOKAAL_S)

    async def _dienst(self, domein: str, dienst: str, data: dict) -> bool:
        try:
            await self.hass.services.async_call(domein, dienst, data, blocking=True)
        except Exception as fout:  # noqa: BLE001 - de service van een merkintegratie kan van alles gooien
            self.hub._log(2, f"{data.get('entity_id')}: {dienst} mislukt ({fout})")
            return False
        return True

    # ------------------------------------------------------------------ Thermostaat en boiler

    def onthoud(self, entity_id: str) -> None:
        """De stand van voor Zonneburen, de eerste keer dat we een apparaat sturen."""
        if entity_id in self.origineel or not (staat := self.hass.states.get(entity_id)) or staat.state in ONBEKEND:
            return
        doel = staat.attributes.get("temperature")
        self.origineel[entity_id] = {"aan": staat.state not in ("off", "idle"), "stand": staat.state,
                                     "temp": doel if isinstance(doel, (int, float)) else None}
        if isinstance(doel, (int, float)) and entity_id not in self.basis:
            self.basis[entity_id] = float(doel)
        self.bewaar()

    async def thermisch(self, i: int, apparaat: dict, wens: str, plan_temp: float | None, instellingen: dict | None = None,
                        forceer: bool = False) -> None:
        """De temperatuur die bij de wens hoort, alleen als die anders is dan wat er staat. Nooit hvac_mode."""
        e = apparaat["host"]
        staat = self.hass.states.get(e)
        if not staat or staat.state in ONBEKEND:
            return
        domein = staat.domain
        a = staat.attributes
        doel_nu = a.get("temperature")
        if not isinstance(doel_nu, (int, float)):
            self.status[i] = "Kan niet sturen: geen doeltemperatuur"
            return
        self.onthoud(e)
        nu = time.time()
        stap = float(a.get("target_temp_step") or (0.5 if domein == "climate" else 1))
        cloud = await self.is_cloud(e)

        # Zelf uitgezet (de thermostaat of in de app)? Dan blijft hij uit: Zonneburen zet niets aan.
        if staat.state == "off" and e not in self.uitgezet:
            self.status[i] = "Staat uit: Zonneburen laat hem met rust"
            return

        # Heeft de bewoner de temperatuur veranderd? Dan is dat de nieuwe basis, en wacht Zonneburen vier uur.
        verwacht = self.limiet.geschreven[e][1] if e in self.limiet.geschreven else self.basis.get(e)
        if not forceer and r.is_handmatig(float(doel_nu), verwacht, self.limiet.op(e), nu, stap, r.NAIJLEN_S if cloud else 60):
            self.basis[e] = float(doel_nu)
            self.pauze_tot[e] = nu + r.HANDMATIG_PAUZE_S
            self.limiet.noteer(e, float(doel_nu), nu)
            self.bewaar()
            self.hub._log(0, f"{apparaat['name'] or e}: je hebt de temperatuur op {doel_nu:g} °C gezet. Dat is nu de basis; "
                             "Zonneburen stuurt dit apparaat de komende vier uur niet")
        if not forceer and self.pauze_tot.get(e, 0) > nu:
            self.status[i] = "Jij stuurt (tot " + time.strftime("%H:%M", time.localtime(self.pauze_tot[e])) + ")"
            return
        basis = self.basis.get(e, float(doel_nu))

        # Besparen hooguit twee uur achter elkaar.
        if wens == r.BESPAREN:
            sinds = self._besparen_sinds.setdefault(e, nu)
            if nu - sinds > r.BESPAREN_MAX_S:
                wens = r.NORMAAL
        else:
            self._besparen_sinds.pop(e, None)
        # Legionella (60 °C of meer) alleen als de boiler dat haalt; anders gewoon opwarmen en het één keer melden.
        tmax = float(a.get("max_temp", 35 if domein == "climate" else 80))
        if domein == "water_heater" and plan_temp is not None and plan_temp >= 60 and tmax < 60:
            if e not in self._legionella_gemeld:
                self._legionella_gemeld.add(e)
                self.hub._log(2, f"{apparaat['name'] or e} haalt geen {plan_temp:g} °C: gebruik de desinfectiefunctie van het toestel")
            plan_temp = None
        # Een boiler die al bijna op temperatuur is, hoeft niet extra te stoken.
        doel = r.thermisch_doel(domein, wens, basis, r.Grenzen(float(a.get("min_temp", 5)), float(a.get("max_temp", 35 if domein == "climate" else 80)),
                                                              stap, staat.state == "cool"), plan_temp, instellingen)
        vorige = self._vorige_wens.get(e)
        self._vorige_wens[e] = wens
        if domein == "water_heater" and wens == r.VERHOGEN:
            huidig = a.get("current_temperature")
            if vorige != r.VERHOGEN:
                self._overgeslagen.discard(e)
                if isinstance(huidig, (int, float)) and huidig >= doel - 2:
                    self._overgeslagen.add(e)
            if e in self._overgeslagen:
                doel = r.thermisch_doel(domein, r.NORMAAL, basis, r.Grenzen(0, 100, stap), None, instellingen)

        if wens == r.UITZETTEN:
            if (staat.state != "off" and await self._mag_schrijven(e, "uit", nu, forceer)
                    and await self._dienst(domein, "turn_off", {"entity_id": e})):
                self.uitgezet.add(e)
                self.limiet.noteer(e, "uit", nu)
                self.bewaar()
            self.status[i] = "Zonneburen stuurt: uit (mag van jou)"
            return
        if e in self.uitgezet and staat.state == "off" and await self._dienst(domein, "turn_on", {"entity_id": e}):
            self.uitgezet.discard(e)
            self.bewaar()

        self.status[i] = {r.VERHOGEN: f"Zonneburen stuurt: warmer ({doel:g} °C)" if staat.state != "cool" else f"Zonneburen stuurt: voorkoelen ({doel:g} °C)",
                          r.BESPAREN: f"Zonneburen stuurt: zuiniger ({doel:g} °C)"}.get(wens, f"Normaal ({doel:g} °C)")
        if self.limiet.zelfde(e, doel) or (e not in self.limiet.geschreven and abs(doel - float(doel_nu)) < stap / 2):
            return
        if not await self._mag_schrijven(e, doel, nu, forceer):
            return
        if await self._dienst(domein, "set_temperature", {"entity_id": e, "temperature": doel}):
            self.limiet.noteer(e, doel, nu)

    async def herstel(self, apparaat: dict, vergeten: bool = False) -> None:
        """Terug naar de basis van de bewoner: bij stoppen, pauze, noodstop of verwijderen. hvac_mode blijft zoals hij is."""
        e = apparaat["host"]
        if not e:
            return
        staat = self.hass.states.get(e)
        if staat and staat.domain in ("climate", "water_heater") and e in self.basis:
            if e in self.uitgezet:
                await self._dienst(staat.domain, "turn_on", {"entity_id": e})
                self.uitgezet.discard(e)
            doel = self.basis[e]
            if (isinstance(staat.attributes.get("temperature"), (int, float)) and abs(staat.attributes["temperature"] - doel) >= 0.25
                    and await self._mag_schrijven(e, doel, time.time(), True)):
                await self._dienst(staat.domain, "set_temperature", {"entity_id": e, "temperature": doel})
            self.limiet.noteer(e, doel, time.time())
        elif staat and vergeten and (orig := self.origineel.get(e)) and orig.get("aan") is not None:
            # Een stekker of schakelaar: terug zoals hij voor Zonneburen stond, maar niet uit terwijl hij draait.
            if orig["aan"] or self.meting(e).mag_uit(time.time()):
                await self._dienst("homeassistant", "turn_on" if orig["aan"] else "turn_off", {"entity_id": e})
        if vergeten:
            for opslag in (self.basis, self.origineel, self.pauze_tot):
                opslag.pop(e, None)
            self.uitgezet.discard(e)
            self.limiet.geschreven.pop(e, None)
            self.bewaar()

    # ------------------------------------------------------------------ Witgoed en stekkers

    def meting(self, entity_id: str) -> r.Stilstand:
        return self.stil.setdefault(entity_id, r.Stilstand())

    def start_slim(self, i: int, uiterlijk: float | None) -> None:
        self.klaargezet[i] = uiterlijk
        self.loopt_door.discard(i)
        self.bewaar()

    def stop_slim(self, i: int) -> None:
        self.klaargezet.pop(i, None)
        self.bewaar()

    async def programma(self, i: int, apparaat: dict, plan_aan: bool | None, duur_s: float | None = None) -> None:
        e = apparaat["host"]
        nu = time.time()
        stil = self.meting(e)
        if (ws := self.hub.vermogen_gemeten(apparaat)) is not None:
            stil.meet(ws, nu)
        aan = self.hub._is_aan(e)
        if aan is None:
            return
        self.onthoud(e)
        if i in self.loopt_door and (not aan or (stil.gemeten and stil.stil_sinds(nu) >= r.KLAAR_S)):
            self.loopt_door.discard(i)
            self.hub._log(1, f"{apparaat['name'] or e}: programma klaar")
        klaargezet = i in self.klaargezet
        uiterlijk = self.klaargezet.get(i)
        wil = r.programma_wens(plan_aan, klaargezet, i in self.loopt_door, aan, stil, nu, False, uiterlijk)
        if klaargezet and wil:
            # Het beste moment: aan, en aan laten tot het programma klaar is.
            self.klaargezet.pop(i, None)
            self.loopt_door.add(i)
            self.bewaar()
            self.hub._log(1, f"{apparaat['name'] or e}: Zonneburen start het programma nu")
        if i in self.loopt_door:
            self.status[i] = "Zonneburen stuurt: programma loopt"
        elif klaargezet:
            self.status[i] = "Wacht op het beste moment" + (
                f" (uiterlijk {time.strftime('%H:%M', time.localtime(uiterlijk))})" if uiterlijk else "")
        else:
            self.status[i] = "Klaar voor gebruik"
        if wil is None or wil == aan:
            return
        await self._dienst("homeassistant", "turn_on" if wil else "turn_off", {"entity_id": e})

    def uitstel_uit(self, apparaat: dict) -> bool:
        """Een stekker met vermogensmeting die nog draait, gaat niet automatisch uit."""
        if not apparaat.get("powerEntity"):
            return False
        stil = self.meting(apparaat["host"])
        if (ws := self.hub.vermogen_gemeten(apparaat)) is not None:
            stil.meet(ws, time.time())
        return not stil.mag_uit(time.time())
