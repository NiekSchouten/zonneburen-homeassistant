# Changelog

## 0.10.0

**Herstart nodig:** ja. Er zijn nieuwe entiteiten: per wasmachine, vaatwasser of droger *slim starten*,
*nu starten* en *klaar om*.

### Apparaten veilig sturen
- Warmtepompen, airco's en elektrische verwarming gaan nooit meer uit. Zonneburen verschuift alleen hun
  temperatuur, binnen comfortgrenzen, en verandert `hvac_mode` nooit.
- Boilers gaan nooit uit. Zonneburen verhoogt alleen de temperatuur, nooit onder je eigen temperatuur.
- Zet je de temperatuur zelf anders, dan wordt dat de nieuwe basis en wacht Zonneburen vier uur.
- Cloud-integraties krijgen hooguit één schrijfactie per half uur per entiteit. De sturing stopt als er
  vandaag minder dan 30 aanroepen over zijn.
- Witgoed op een stekker:
  - De stekker gaat nooit uit terwijl het apparaat draait.
  - Met *slim starten* en *klaar om* draait het programma één keer, op het beste moment voor je eindtijd.
  - Het programma loopt in één keer door tot het klaar is, en daarna blijft de stekker aan.
- Na *terug naar slim regelen* verandert er pas iets bij het volgende blok in het plan.
- Bij stoppen, verwijderen of herladen gaat alles terug naar je eigen stand. Hetzelfde gebeurt als de
  verbinding langer dan 10 minuten weg is.
- Elk gekoppeld apparaat heeft meteen zijn plan-sensor, ook voordat er een plan is.

## 0.9.0

**Herstart nodig:** ja. Er zijn nieuwe instellingen en de configuratie wordt omgezet.

### Deye / Sunsynk: volledig sturen
- De integratie stuurt nu de volledige Deye (niveau A). Dat omvat de werkmodus, het programmavermogen, de maximale laad- en ontlaadstroom, netladen aan of uit en de maximale export.
- Standen:
  - Netladen gebruikt het doel en het vermogen uit het plan.
  - Zelfverbruik zet de omvormer op Zero Export To CT, tot je minimale lading.
  - Vasthouden en alleen zon zetten de ondergrens op de lading van nu.
  - Standby zet alle stromen op 0.
  - Netontladen gebruikt Export First, maar alleen als je *terugleveren toestaan* aanzet.
- Je eigen instellingen worden bewaard. Ze gaan terug bij uitzetten, herladen of verwijderen, en bij een pauze, een noodstop of als er geen plan is. Bij het afsluiten van Home Assistant gaan ze niet terug.
- Zuinig schrijven:
  - Elke instelling hooguit één keer per vijf minuten, behalve bij een nieuwe stand.
  - Kleine verschillen worden niet geschreven.
  - Elke opdracht heeft een time-out van 10 s.
  - Na drie mislukte pogingen krijg je een reparatiemelding en gaan je eigen instellingen terug.
- De hoofdzekering wordt bewaakt met fasestroomsensoren, in stappen van 10 A per 10 s.
- Aan de webapp meldt de integratie nu ook je minimale lading, de maximale lading van het net, het maximale laad- en ontlaadvermogen en dat de omvormer zelf op zijn meter regelt.
- Migratie: stond je Deye nog op het profiel vermogen (bijvoorbeeld `input_number.zonneburen_accu_vermogen`), dan wordt hij automatisch omgezet naar tijdprogramma's.

## 0.8.2
- Deye volgt de stand van het plan, niet het teken van het vermogen.
