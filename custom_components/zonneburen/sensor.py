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
    voeg_toe([WijkOverschot(hub), Beursprijs(hub), GedeeldDezeWeek(hub), Energieplan(hub), VerwachteBesparing(hub)])

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
    plan = hub.ems.plan
    if not plan:
        return set()
    indexen = {l["apparaat"] for l in plan.lasten if isinstance(l.get("apparaat"), int) and not isinstance(l.get("apparaat"), bool)}
    return {i for i in indexen | set(plan.rust) if 0 <= i < protocol.MAX_APPARATEN}


def namen(hub) -> dict[int, str]:
    return {i: a["name"] for i, a in enumerate(hub._apparaten()) if a.get("name")}


def klok(unix: float, nu: float) -> str:
    """02:15, of "morgen 02:15" als het niet meer vandaag is."""
    t, vandaag = dt_util.as_local(dt_util.utc_from_timestamp(unix)), dt_util.as_local(dt_util.utc_from_timestamp(nu))
    dagen = (t.date() - vandaag.date()).days
    return t.strftime("%H:%M") if dagen <= 0 else ("morgen " if dagen == 1 else "overmorgen ") + t.strftime("%H:%M")


class WijkOverschot(ZonneburenEntiteit, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, hub) -> None:
        super().__init__(hub, "wijk_overschot")

    @property
    def native_value(self) -> float | None:
        return self.hub.wijk_overschot


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
    _unrecorded_attributes = frozenset({"blokken", "apparaatblokken", "kwartieren"})

    def __init__(self, hub) -> None:
        super().__init__(hub, "energieplan")

    @property
    def native_value(self) -> str | None:
        laatste = self.hub.ems.laatste
        if not self.hub.ems.plan and not laatste:
            return None
        return ems_core.ZINNEN[laatste.stand] if laatste else None

    @property
    def extra_state_attributes(self) -> dict:
        ems = self.hub.ems
        attributen = ems.info()
        if ems.laatste and ems.laatste.afwijking:
            attributen["afwijking"] = ems.laatste.afwijking
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


class VerwachteBesparing(ZonneburenEntiteit, SensorEntity):
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, hub) -> None:
        super().__init__(hub, "verwachte_besparing")

    @property
    def native_value(self) -> float | None:
        plan = self.hub.ems.plan
        return plan.verwacht.get("besparingBatterij") if plan else None

    @property
    def extra_state_attributes(self) -> dict | None:
        plan = self.hub.ems.plan
        return {"door_slim_sturen": plan.verwacht.get("besparingSturen")} if plan and plan.verwacht else None


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
