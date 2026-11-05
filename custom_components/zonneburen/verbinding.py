"""Eigen verbinding met de broker van Zonneburen.

Los van de MQTT-integratie van Home Assistant: die praat met de broker van de bewoner zelf. Dit
account geldt alleen voor dit huis, met TLS en certificaatcontrole, zoals op de hub.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import paho.mqtt.client as mqtt
from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import client_context

from . import protocol
from .const import CONF_ADRES, CONF_APPARAAT_ID, CONF_MQTT_GEBRUIKER, CONF_MQTT_HOST, CONF_MQTT_POORT, CONF_MQTT_WACHTWOORD

_LOGGER = logging.getLogger(__name__)


class Verbinding:
    def __init__(self, hass: HomeAssistant, gegevens: dict, bij_bericht: Callable[[str, bytes], None],
                 bij_status: Callable[[bool], None]) -> None:
        self._hass = hass
        self._gegevens = gegevens
        self._bij_bericht = bij_bericht
        self._bij_status = bij_status
        self._client: mqtt.Client | None = None
        self.adres: str = gegevens[CONF_ADRES]
        self.verbonden = False

    @property
    def abonnementen(self) -> list[tuple[str, int]]:
        return [
            (protocol.huis_topic(self.adres, "hub/opdracht"), 1),
            # Het energieplan: retained, dus na elke (her)verbinding meteen het laatste plan.
            (protocol.huis_topic(self.adres, "hub/plan"), 1),
            # Om een tweede hub op dit adres te zien: de wijk telt er maar één.
            (protocol.huis_topic(self.adres, "telemetrie"), 0),
            (protocol.TOPIC_SCHAKELEN, 1),
            (protocol.TOPIC_NOODSTOP, 1),
            (protocol.TOPIC_WEEKRAPPORT, 0),
            (protocol.TOPIC_WIJK_STATUS, 0),
            (protocol.TOPIC_PRIJZEN, 0),
        ]

    def start(self) -> None:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"zb-{self._gegevens[CONF_APPARAAT_ID]}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self._gegevens[CONF_MQTT_GEBRUIKER], self._gegevens[CONF_MQTT_WACHTWOORD])
        client.tls_set_context(client_context())
        client.will_set(protocol.status_topic(self.adres), protocol.bouw_status(self.adres, False), qos=1, retain=True)
        client.reconnect_delay_set(5, 120)
        client.on_connect = self._verbonden
        client.on_disconnect = self._verbroken
        client.on_message = self._bericht
        client.connect_async(self._gegevens[CONF_MQTT_HOST], int(self._gegevens[CONF_MQTT_POORT]), keepalive=45)
        client.loop_start()
        self._client = client

    async def async_stop(self) -> None:
        if not self._client:
            return
        client, self._client = self._client, None
        if self.verbonden:
            client.publish(protocol.status_topic(self.adres), protocol.bouw_status(self.adres, False), qos=1, retain=True)

        def stop() -> None:
            client.disconnect()
            client.loop_stop()

        await self._hass.async_add_executor_job(stop)

    def publiceer(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        if self._client and self.verbonden:
            self._client.publish(topic, payload, qos=qos, retain=retain)

    # De callbacks hieronder draaien in de thread van paho; alles gaat via de event loop verder.

    def _verbonden(self, client: mqtt.Client, _userdata, _flags, reden, _properties) -> None:
        if reden.is_failure:
            _LOGGER.warning("Broker van Zonneburen weigert de verbinding: %s", reden)
            return
        client.subscribe(self.abonnementen)
        client.publish(protocol.status_topic(self.adres), protocol.bouw_status(self.adres, True), qos=1, retain=True)
        self.verbonden = True
        self._hass.loop.call_soon_threadsafe(self._bij_status, True)

    def _verbroken(self, _client, _userdata, _flags, reden, _properties) -> None:
        self.verbonden = False
        if reden.is_failure:
            _LOGGER.info("Verbinding met Zonneburen verbroken (%s); paho probeert het opnieuw", reden)
        self._hass.loop.call_soon_threadsafe(self._bij_status, False)

    def _bericht(self, _client, _userdata, bericht: mqtt.MQTTMessage) -> None:
        self._hass.loop.call_soon_threadsafe(self._bij_bericht, bericht.topic, bericht.payload)
