"""Vaste waarden van de Zonneburen-integratie."""

DOMAIN = "zonneburen"
VERSIE = "0.8.1"

# De webapp herkent hieraan een software-hub: geen firmware, bijwerken gaat via de Marketplace.
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

# Het energieplan: welke entiteit de batterij stuurt, en de lokale grenzen.
CONF_EMS_AAN = "ems_aan"
CONF_EMS_VERMOGEN = "ems_vermogen"
CONF_EMS_NETDOEL = "ems_netdoel"
CONF_EMS_LADEN_POSITIEF = "ems_laden_positief"
CONF_EMS_MODUS = "ems_modus"
CONF_EMS_MODUS_STUREN = "ems_modus_sturen"
CONF_EMS_MODUS_TERUG = "ems_modus_terug"
CONF_EMS_MAX_LAAD_W = "ems_max_laad_w"
CONF_EMS_MAX_ONTLAAD_W = "ems_max_ontlaad_w"
CONF_EMS_ZEKERING_A = "ems_zekering_a"
# Batterijen met een apart getal om te ontladen (CONF_EMS_VERMOGEN is dan het laadvermogen), en een
# keuze voor de richting, zoals Marstek of SolarEdge.
CONF_EMS_ONTLAAD = "ems_ontlaad"
CONF_EMS_RICHTING = "ems_richting"
CONF_EMS_RICHTING_LADEN = "ems_richting_laden"
CONF_EMS_RICHTING_ONTLADEN = "ems_richting_ontladen"
CONF_EMS_RICHTING_STOP = "ems_richting_stop"
# Hoe de batterij gestuurd wordt: met een vermogen (number in W, ook netdoel of apart laden/ontladen), of
# met de tijdprogramma's van een Deye of Sunsynk (Time of Use), die geen vermogens-setpoint kennen.
CONF_EMS_PROFIEL = "ems_profiel"
PROFIEL_VERMOGEN = "vermogen"
PROFIEL_DEYE = "deye_tou"
CONF_TOU = "ems_tou"                    # select: Time of Use aan/uit
CONF_TOU_LADEN = "ems_tou_laden"        # 6 selects: program_N_charging (Grid / Disabled)
CONF_TOU_SOC = "ems_tou_soc"            # 6 numbers: program_N_soc (%)
CONF_TOU_TIJD = "ems_tou_tijd"          # 6 times: program_N_time
CONF_TOU_STROOM = "ems_tou_stroom"      # number: laadstroom van het net (A)
CONF_TOU_SPANNING = "ems_tou_spanning"  # sensor: accuspanning (V)
CONF_TOU_MIN_SOC = "ems_tou_min_soc"    # number: de ondergrens van de omvormer (battery_low_soc), optioneel
CONF_TOU_MAX_PCT = "ems_tou_max_pct"    # hoe vol netladen hooguit mag (standaard 90 %)
CONF_TOU_MIN_PCT = "ems_tou_min_pct"    # de ondergrens als de omvormer er geen meldt (standaard 20 %)
EMS_OPTIES = (CONF_EMS_AAN, CONF_EMS_VERMOGEN, CONF_EMS_NETDOEL, CONF_EMS_LADEN_POSITIEF, CONF_EMS_MODUS, CONF_EMS_MODUS_STUREN,
              CONF_EMS_MODUS_TERUG, CONF_EMS_MAX_LAAD_W, CONF_EMS_MAX_ONTLAAD_W, CONF_EMS_ZEKERING_A, CONF_EMS_ONTLAAD,
              CONF_EMS_RICHTING, CONF_EMS_RICHTING_LADEN, CONF_EMS_RICHTING_ONTLADEN, CONF_EMS_RICHTING_STOP,
              CONF_EMS_PROFIEL, CONF_TOU, CONF_TOU_LADEN, CONF_TOU_SOC, CONF_TOU_TIJD, CONF_TOU_STROOM, CONF_TOU_SPANNING,
              CONF_TOU_MIN_SOC, CONF_TOU_MAX_PCT, CONF_TOU_MIN_PCT)

# Slimme meters met afname en teruglevering in twee sensoren (DSMR, P1 Monitor): het net is het verschil.
CONF_NET_TERUG = "net_terug"

SENSOREN = (CONF_NET, CONF_NET_TERUG, CONF_ZON, CONF_BATTERIJ, CONF_SOC, CONF_VERBRUIK)

SIGNAAL_BIJGEWERKT = f"{DOMAIN}_bijgewerkt"
