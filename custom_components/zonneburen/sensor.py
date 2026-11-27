"""Wat de wijk meldt: overschot, beursprijs en het weekrapport."""

from __future__ import annotations

import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ZonneburenConfigEntry, ems_core, protocol
from .const import SIGNAAL_BIJGEWERKT
from .entiteit import ZonneburenEntiteit


async def async_setup_entry(hass: HomeAssistant, entry: ZonneburenConfigEntry, voeg_toe: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    voeg_toe([WijkOverschot(hub), Beursprijs(hub), GedeeldDezeWeek(hub), Energieplan(hub), VerwachteBesparing(hub), Buurtbatterij(hub)])

    # Per apparaat dat in het energieplan staat een sensor met zijn volgende blok. Het plan komt pas
    # na het verbinden binnen en kan later apparaten toevoegen: daarom bij elke verandering kijken.
    bekend: set[int] = set()

    @callback
    def nieuwe_apparaten() -> None:
        nieuw = sorted(apparaten_in_plan(hub) - bekend)
        if nieuw:
            bekend.update(nieuw)
            voeg_toe([ApparaatPlan(hub, i) for i in nieuw])

    nieuwe_apparaten()
    entry.async_on_unload(async_dispatcher_connect(hass, f"{SIGNAAL_BIJGEWERKT}_{entry.entry_id}", nieuwe_apparaten))


def apparaten_in_plan(hub) -> set[int]:
    # Ook zonder plan (net opgestart): elk gekoppeld apparaat krijgt zijn sensor, anders staat hij op "niet beschikbaar".
    gekoppeld = {i for i, a in enumerate(hub._apparaten()) if a["enabled"] and a["host"] and not str(a["host"]).startswith("light.")
                 and a.get("role") != protocol.ROL_LICHT}
    plan = hub.ems.plan
    if not plan:
        return gekoppeld
    indexen = {l["apparaat"] for l in plan.lasten if isinstance(l.get("apparaat"), int) and not isinstance(l.get("apparaat"), bool)}
    return {i for i in indexen | set(plan.rust) | set(plan.soorten) | gekoppeld if 0 <= i < protocol.MAX_APPARATEN}


def namen(hub) -> dict[int, str]:
    return {i: a["name"] for i, a in enumerate(hub._apparaten()) if a.get("name")}


def klok(unix: float, nu: float) -> str:
    """02:15, of "morgen 02:15" als het niet meer vandaag is."""
    t, vandaag = dt_util.as_local(dt_util.utc_from_timestamp(unix)), dt_util.as_local(dt_util.utc_from_timestamp(nu))
    dagen = (t.date() - vandaag.date()).days
    return t.strftime("%H:%M") if dagen <= 0 else ("morgen " if dagen == 1 else "overmorgen ") + t.strftime("%H:%M")


class WijkOverschot(ZonneburenEntiteit, SensorEntity):
    """Het overschot van de wijk, met de huizen en de ranglijst voor de wijkkaart als attributen."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _unrecorded_attributes = frozenset({"huizen", "ranglijst", "buurtbatterij"})

    def __init__(self, hub) -> None:
        super().__init__(hub, "wijk_overschot")

    @property
    def native_value(self) -> float | None:
        return self.hub.wijk_overschot

    @property
    def extra_state_attributes(self) -> dict | None:
        # mijn_adres: zo markeert de wijkkaart je eigen huis zonder dat je iets instelt.
        return {"zonneburen": "wijk", "mijn_adres": self.hub.adres, **self.hub.wijk}


class Buurtbatterij(ZonneburenEntiteit, SensorEntity):
    """De thuisbatterijen van de deelnemers samen: hoe vol, en wat ze nu met de wijk delen."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-battery-outline"

    def __init__(self, hub) -> None:
        super().__init__(hub, "buurtbatterij")

    @property
    def _bb(self) -> dict:
        bb = self.hub.wijk.get("buurtbatterij")
        return bb if isinstance(bb, dict) else {}

    @property
    def native_value(self) -> float | None:
        procent = self._bb.get("procent")
        return procent if isinstance(procent, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"zonneburen": "buurtbatterij", "gedeeld_nu_watt": self.hub.wijk.get("gedeeld_nu_watt"), **self._bb}


class Beursprijs(ZonneburenEntiteit, SensorEntity):
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_suggested_display_precision = 3

    def __init__(self, hub) -> None:
        super().__init__(hub, "beursprijs")

    @property
    def native_value(self) -> float | None:
        return protocol.prijs_nu(self.hub.prijzen, time.time())


class GedeeldDezeWeek(ZonneburenEntiteit, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, hub) -> None:
        super().__init__(hub, "gedeeld_week")

    @property
    def native_value(self) -> float | None:
        return self.hub.week["gedeeld_kwh"] if self.hub.week else None

    @property
    def extra_state_attributes(self) -> dict | None:
        return {"plek": self.hub.week["plek"], "huizen": self.hub.week["huizen"]} if self.hub.week else None


class Energieplan(ZonneburenEntiteit, SensorEntity):
    """Wat de batterij nu volgens het plan doet, met de komende blokken als attributen (voor de kaart)."""

    _attr_icon = "mdi:battery-clock"
    # Het hele plan hoort niet elk kwartier in de database van de recorder.
    _unrecorded_attributes = frozenset({"blokken", "apparaatblokken", "kwartieren", "schema", "schema_lasten"})

    def __init__(self, hub) -> None:
        super().__init__(hub, "energieplan")

    def _schema_nu(self, nu: float) -> list | None:
        """De rij van dit kwartier uit het schema, of None."""
        schema = self.hub.ems.schema
        if not schema:
            return None
        return next((r for r in schema["rijen"] if r[0] <= nu < r[0] + 900), None)

    @property
    def native_value(self) -> str | None:
        laatste = self.hub.ems.laatste
        if self.hub.ems.plan and laatste:
            return ems_core.ZINNEN[laatste.stand]
        # Alleen advies: dan wat het plan nu zou doen.
        if (rij := self._schema_nu(time.time())) is not None:
            return ems_core.ZINNEN.get(rij[1], rij[1])
        return ems_core.ZINNEN[laatste.stand] if laatste else None

    @property
    def extra_state_attributes(self) -> dict:
        ems = self.hub.ems
        attributen = ems.info()
        attributen["zonneburen"] = "energieplan"
        if ems.laatste and ems.laatste.afwijking:
            attributen["afwijking"] = ems.laatste.afwijking
        nu = time.time()
        if schema := ems.schema:
            # Het hele schema, vanaf dit kwartier: [begin, stand, prijsIn, prijsUit, zonW, verbruikW, batW, netW,
            # netDoelW, socEind, voorspeld]. Voor de kaart Per kwartier.
            rijen = [r for r in schema["rijen"] if r[0] + 900 > nu][:192]
            attributen.update(schema=rijen, schema_lasten=[l for l in schema["lasten"] if l["tot"] > nu],
                              advies=not schema["sturen"], schema_plan_id=schema["plan_id"])
            if not ems.plan:
                attributen.update(
                    plan_id=schema["plan_id"], geldig_tot=schema["geldig_tot"],
                    blokken=schema_blokken(rijen),
                    apparaatblokken=[{"i": l.get("i"), "naam": l["naam"], "van": l["van"], "tot": l["tot"], "stand": l["stand"],
                                      "zin": ems_core.BLOK_ZINNEN.get(l["stand"], "Aan")} for l in attributen["schema_lasten"]],
                    kwartieren=[[r[0], r[1], r[9], r[2]] for r in rijen[:144]])
        if ems.plan:
            nu = time.time()
            attributen.update(
                plan_id=ems.plan.plan_id, geldig_tot=ems.plan.geldig_tot,
                blokken=ems_core.blokken(ems.plan, nu),
                apparaatblokken=ems_core.apparaat_blokken(ems.plan, nu, namen(self.hub)),
                # Compact voor de tijdlijn op de kaart: [begin, stand, lading aan het eind, inkoopprijs]
                kwartieren=[[ems.plan.t0 + i * ems.plan.stap, k.stand, k.soc_eind, k.prijs_in]
                            for i, k in enumerate(ems.plan.kwartieren) if ems.plan.t0 + (i + 1) * ems.plan.stap > nu][:144],
            )
        return attributen


def schema_blokken(rijen: list, maximaal: int = 6) -> list[dict]:
    """Opeenvolgende kwartieren met dezelfde stand samen, zoals ems_core.blokken voor het plan."""
    uit: list[dict] = []
    for r in rijen:
        if uit and uit[-1]["stand"] == r[1] and uit[-1]["tot"] == r[0]:
            uit[-1]["tot"] = r[0] + 900
        elif len(uit) < maximaal:
            uit.append({"stand": r[1], "van": r[0], "tot": r[0] + 900, "zin": ems_core.ZINNEN.get(r[1], r[1])})
        else:
            break
    return uit


class VerwachteBesparing(ZonneburenEntiteit, SensorEntity):
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, hub) -> None:
        super().__init__(hub, "verwachte_besparing")

    def _verwacht(self) -> dict:
        plan, schema = self.hub.ems.plan, self.hub.ems.schema
        return (plan.verwacht if plan else None) or (schema["verwacht"] if schema else None) or {}

    @property
    def native_value(self) -> float | None:
        return self._verwacht().get("besparingBatterij")

    @property
    def extra_state_attributes(self) -> dict | None:
        verwacht = self._verwacht()
        return {"door_slim_sturen": verwacht.get("besparingSturen")} if verwacht else None


class ApparaatPlan(ZonneburenEntiteit, SensorEntity):
    """Wat het energieplan met één apparaat van plan is: "Laden 02:15-04:30"."""

    _attr_icon = "mdi:calendar-clock"
    _unrecorded_attributes = frozenset({"blokken"})

    def __init__(self, hub, index: int) -> None:
        super().__init__(hub, f"plan_apparaat_{index}")
        self.index = index
        self._attr_translation_key = None
        self._attr_name = f"Energieplan {namen(hub).get(index) or f'apparaat {index + 1}'}"

    def _blokken(self, nu: float) -> list[dict]:
        return ems_core.apparaat_blokken(self.hub.ems.plan, nu, namen(self.hub), self.index)

    @property
    def native_value(self) -> str:
        nu = time.time()
        blokken = self._blokken(nu)
        if blokken and blokken[0]["van"] <= nu:
            return f"Nu: {blokken[0]['zin']} tot {klok(blokken[0]['tot'], nu)}"
        if blokken:
            return f"{blokken[0]['zin']} {klok(blokken[0]['van'], nu)}–{klok(blokken[0]['tot'], blokken[0]['van'])}"
        if self.hub.ems.apparaat(self.index).soort == "rust":
            return "Wacht op het beste moment"
        return "Geen blok gepland"

    @property
    def extra_state_attributes(self) -> dict:
        nu = time.time()
        blokken = self._blokken(nu)
        stand = self.hub.ems.apparaat(self.index)
        return {
            "apparaat": self.index, "volgt_plan": stand.soort == "plan", "aan_volgens_plan": stand.aan,
            "volgende_van": blokken[0]["van"] if blokken else None, "volgende_tot": blokken[0]["tot"] if blokken else None,
            "blokken": blokken[:12],
        }
