"""Vaste waarden van de Zonneburen-integratie."""

DOMAIN = "zonneburen"
VERSIE = "0.1.0"

# De webapp herkent hieraan een software-hub: geen firmware, bijwerken gaat via HACS.
FIRMWARE = f"ha-{VERSIE}"
STANDAARD_SERVER = "https://zonopzwaagdijk.nl"

# Uit het koppelen; staan in entry.data.
CONF_SERVER = "server"
CONF_ADRES = "adres"
CONF_APPARAAT_ID = "apparaat_id"
CONF_MQTT_HOST = "mqtt_host"
CONF_MQTT_POORT = "mqtt_poort"
CONF_MQTT_GEBRUIKER = "mqtt_gebruiker"
CONF_MQTT_WACHTWOORD = "mqtt_wachtwoord"

# Keuzes van de bewoner; staan in entry.options, zodat de webapp ze ook kan wijzigen.
CONF_NET = "net"
CONF_ZON = "zon"
CONF_BATTERIJ = "batterij"
CONF_SOC = "soc"
CONF_VERBRUIK = "verbruik"
CONF_NET_OM = "net_omdraaien"
CONF_BATTERIJ_OM = "batterij_omdraaien"
CONF_BEDIENING = "bediening"
CONF_WIJK_AUTOMATISCH = "wijk_automatisch"
CONF_APPARATEN = "apparaten"
CONF_NAAM = "naam"
CONF_OVERIG = "overig"

SENSOREN = (CONF_NET, CONF_ZON, CONF_BATTERIJ, CONF_SOC, CONF_VERBRUIK)

SIGNAAL_BIJGEWERKT = f"{DOMAIN}_bijgewerkt"
