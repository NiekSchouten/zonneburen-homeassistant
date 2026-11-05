# Zonneburen voor Home Assistant

Maak van je Home Assistant de hub van je huis in de energiewijk Zonneburen. Je hebt
dan geen Zonneburen Link nodig: Home Assistant doet hetzelfde, met alles wat het al van je huis weet.

## Wat het doet

- **Meedoen met de wijk.** Elke minuut gaat je netverbruik naar de wijk, net als bij de hub. Heeft de
  wijk stroom over, dan zet Home Assistant je boiler, laadpaal of ander apparaat aan, en bevestigt dat.
- **De webapp werkt gewoon.** Live meekijken, Inzichten, Meldingen en Apparaten: de webapp ziet Home
  Assistant als je hub.
- **Elk apparaat uit Home Assistant.** Kies in de webapp bij Apparaten een schakelaar, lamp,
  thermostaat of boiler uit Home Assistant. De lijst staat er direct, ook in een groot huis.
- **Noodstop van het bestuur** zet alles terug op normaal.
- **In Home Assistant zelf** zie je of je verbonden bent, of er wijkstroom loopt, het overschot in de
  wijk, de beursprijs en wat je deze week deelde.

## Installeren

1. Open HACS, kies rechtsboven de drie puntjes en dan **Aangepaste repositories**.
2. Vul `https://github.com/NiekSchouten/zonneburen-homeassistant` in, kies **Integratie** en voeg toe.
3. Zoek **Zonneburen** in HACS, installeer het en herstart Home Assistant.

## Koppelen

1. Maak in de webapp bij **Mijn hub** een koppelcode aan.
2. Ga in Home Assistant naar **Instellingen → Apparaten en diensten → Integratie toevoegen** en kies
   **Zonneburen**.
3. Vul de koppelcode in.
4. Kies je sensoren. Alleen het net is nodig: positief is afname. Zon, batterij en verbruik maken de
   grafieken in de webapp vollediger.

Gebruik je nog de Zonneburen-blueprint of een Zonneburen Link? Zet die dan uit: twee zenders op één
adres tellen je verbruik dubbel.

## Energieplan

Vanaf 0.3.0 kan Home Assistant het energieplan uit de webapp uitvoeren. De server rekent elk
kwartier een plan voor je batterij; de integratie voert het kwartier van nu uit en regelt elke vijf
seconden bij op je netsensor.

1. Zet in de webapp bij *Energieplan* de stand op *Sturen*.
2. Open in Home Assistant de opties van Zonneburen en klap *Energieplan* open.
3. Kies de entiteit waarmee je batterij-integratie het vermogen instelt (een `number`, in W).
   Is het een netdoel, zoals het grid setpoint van een Victron ESS, vink dat dan aan.
4. Moet de batterij eerst in een stand voor externe sturing, kies dan de `select`-entiteit en de
   opties om te sturen en om terug te zetten.
5. Zet *Energieplan uitvoeren* aan.

Je krijgt er een sensor *Energieplan* bij (wat de batterij nu doet, met de komende blokken als
attributen), *Verwachte besparing*, een schakelaar *Energieplan uitvoeren* en knoppen om twee uur te
pauzeren of te hervatten. In het dashboard laat de kaart `custom:zonneburen-energieplan-card` het
plan als tijdlijn zien.

Zonder geldig plan, bij een pauze of als je netsensor langer dan een minuut niets meldt, zet de
integratie het vermogen op 0 en de batterij terug in zijn eigen stand. Draait er ook EMHASS, evcc of
een ander EMS, dan krijg je een melding: twee regelaars werken elkaar tegen. Een plan dat niet door
de controle komt, wordt geweigerd; het vorige blijft dan gelden.

## Goed om te weten

- De verbinding met de wijk loopt over een eigen account op de broker van Zonneburen, met TLS. Dat
  staat los van een MQTT-broker die je zelf in Home Assistant gebruikt.
- Wat je in Home Assistant zelf schakelt, blijft staan. Alleen een wijkcommando, de noodstop of een
  keuze in de webapp verandert iets.
- Bijwerken gaat via HACS; de webapp biedt geen firmware aan.
- Nog niet mogelijk: witgoed plannen en de eigen regels van de hub (eerst je eigen zon, piekuren,
  prijsgrens). Die komen in een volgende versie.

## Problemen

Kies bij de integratie **Diagnose downloaden** en stuur dat bestand mee met je vraag. Het wachtwoord
van de broker staat er niet in.
