# Changelog

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
