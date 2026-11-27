<p align="center">
  <img src="custom_components/zonneburen/brand/icon.png" alt="Zonneburen" width="128">
</p>

# Zonneburen voor Home Assistant

Maak van je Home Assistant de hub van je huis in de energiewijk Zonneburen. Je hebt dan geen
Zonneburen Link nodig: Home Assistant doet hetzelfde, met alles wat het al van je huis weet.

**In het kort:** installeren via de Marketplace → koppelcode uit de webapp invullen → controleren
wat Home Assistant zelf heeft gevonden. Klaar in vijf minuten.

## Wat het doet

- **Meedoen met de wijk.** Elke minuut gaat je netverbruik naar de wijk. Heeft de wijk stroom over,
  dan zet Home Assistant je boiler, laadpaal of ander apparaat aan.
- **De webapp werkt gewoon.** Live meekijken, Inzichten, Meldingen en Apparaten: de webapp ziet Home
  Assistant als je hub.
- **Elk apparaat uit Home Assistant.** Kies in de webapp bij *Apparaten* een schakelaar, lamp,
  thermostaat of boiler uit Home Assistant.
- **Je thuisbatterij slim laden** met het energieplan uit de webapp (optioneel, zie hieronder).
- **Noodstop van het bestuur** zet alles terug op normaal.

## Wat heb je nodig

- Home Assistant **2026.3 of nieuwer** (oudere versies werken vanaf 2024.12, maar tonen het logo niet).
- Een account in de Zonneburen-webapp.
- Je slimme meter in Home Assistant, bijvoorbeeld via HomeWizard, DSMR, P1 Monitor, SlimmeLezer of
  je omvormer.

## 1. Installeren

Sinds Home Assistant 2026.11 heet HACS de **Marketplace** en zit het in Home Assistant zelf.

[![Zonneburen openen in de Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=NiekSchouten&repository=zonneburen-homeassistant&category=integration)

De knop opent Zonneburen meteen in je eigen Home Assistant. Werkt hij niet, voeg het dan zelf toe:

1. Ga naar **Instellingen → Marketplace**.
2. Kies **Add from link** (toevoegen via link) en vul in:
   `https://github.com/NiekSchouten/zonneburen-homeassistant`
   De eerste keer vraagt Home Assistant om in te loggen bij GitHub; dat leest alleen openbare gegevens.
3. Open **Zonneburen**, kies **Downloaden** en herstart Home Assistant.

<details>
<summary>Heb je Home Assistant 2026.10 of ouder (nog met HACS)?</summary>

1. Open **HACS**, kies rechtsboven de drie puntjes en dan **Aangepaste repositories**.
2. Vul `https://github.com/NiekSchouten/zonneburen-homeassistant` in, kies **Integratie** en voeg toe.
3. Zoek **Zonneburen**, download het en herstart Home Assistant.

</details>

## 2. Koppelen

[![Zonneburen toevoegen aan Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zonneburen)

Klik op de knop hierboven, of ga naar **Instellingen → Apparaten en diensten → Integratie
toevoegen** en kies **Zonneburen**.

1. **Koppelcode.** Maak in de webapp bij **Mijn hub** een koppelcode aan en vul die in. Het adres
   van de webapp laat je staan.
2. **Je meters.** Home Assistant zoekt zelf je slimme meter, zonnepanelen en thuisbatterij, en laat
   zien wat ze nu meten, bijvoorbeeld *Net: nu 350 W afname*. Klopt het? Klik op **Verzenden**.
3. **Apparaten.** Gevonden warmtepompen, boilers, laadpalen en airco's staan in een lijst. Vink aan
   wat mag meedoen als de wijk stroom over heeft. Er verandert niets aan hoe ze nu staan.
4. **Thuisbatterij.** Kan Home Assistant je batterij sturen, dan zie je hoe, en hoef je alleen
   *Ja, laat Zonneburen mijn batterij sturen* aan te vinken. Heeft je batterij een stand voor
   externe sturing, dan zijn die standen al voorgekozen.

Stappen waarvoor niets gevonden is, slaat Home Assistant over. Onder het apparaat **Zonneburen** zie
je daarna of je verbonden bent, of er wijkstroom loopt, het overschot in de wijk, de beursprijs en
wat je deze week deelde.

<details>
<summary>Waar zoekt Home Assistant?</summary>

1. **Het Energie-dashboard.** Heb je daar bij je net, zon en batterij een vermogenssensor ingesteld,
   dan neemt Zonneburen die over, met het goede teken.
2. **Bekende merken**, zoals HomeWizard, DSMR, P1 Monitor, Enphase, SolarEdge, Victron, Huawei,
   Marstek, Quatt, Alfen en Easee.
3. **De naam van de sensor**, het liefst op hetzelfde apparaat als de meters uit het Energie-dashboard.

Batterijen worden op drie manieren gestuurd: met één vermogen (positief ontladen, negatief laden),
met een netdoel (zoals het grid setpoint van een Victron ESS), of met aparte getallen voor laden en
ontladen plus een keuze voor de richting (zoals Marstek en SolarEdge).

Herkent Home Assistant iets niet goed, kies het dan zelf: alle velden blijven gewoon aan te passen.

</details>

> **Let op:** draait er nog een Zonneburen Link of de oude Zonneburen-blueprint? Zet die dan uit (de
> blueprint onder **Instellingen → Automatiseringen en scènes**). Twee zenders op één adres tellen je
> verbruik dubbel; Home Assistant waarschuwt je als dat gebeurt.

Wil je later iets aanpassen of een nieuw apparaat toevoegen? Kies bij de integratie **Configureren**.
Je krijgt dan een menu: *Meters*, *Apparaten zoeken* en *Thuisbatterij laten sturen*.

## 3. Energieplan voor je batterij (optioneel)

De server maakt elk kwartier een plan voor je batterij: laden als stroom goedkoop is, ontladen als
hij duur is. Home Assistant voert dat plan uit en stuurt elke vijf seconden bij op je slimme meter.

1. Zet in de webapp bij **Energieplan** de stand op **Sturen**.
2. Heb je dat bij het koppelen nog niet gedaan: kies in Home Assistant bij Zonneburen **Configureren →
   Thuisbatterij laten sturen**.
3. Vink **Ja, laat Zonneburen mijn batterij sturen** aan en klik op **Verzenden**.

Home Assistant heeft de entiteiten van je batterij al ingevuld. Wil je ze zien of aanpassen, klap dan
**Details** open; onder elk veld staat wat het betekent.

Je krijgt er een sensor **Energieplan** bij (wat de batterij nu doet, met de komende blokken voor de
batterij en de apparaten als attributen), **Verwachte besparing**, een schakelaar **Energieplan uitvoeren** en knoppen om twee uur
te pauzeren of te hervatten.

**Altijd veilig.** Zonder geldig plan, bij een pauze, een noodstop of als je netsensor langer dan
een minuut niets meldt, zet de integratie het vermogen op 0 en de batterij terug in zijn eigen stand.
Een plan dat niet door de controle komt, wordt geweigerd; het vorige blijft dan gelden. Draait er ook
EMHASS, evcc of een ander energiebeheer, dan krijg je een melding: twee regelaars werken elkaar tegen.

**Deye of Sunsynk.** Zo'n omvormer regelt zelf op zijn CT-meter en heeft zes tijdprogramma's (Time of Use,
bijvoorbeeld via de Solarman-integratie). Herkent Home Assistant ze, dan kies je bij *Thuisbatterij laten
sturen* het profiel **Deye / Sunsynk: tijdprogramma's**. Bestaande installaties die nog op een vermogensregelaar
stonden, worden bij de update automatisch omgezet. Zonneburen stuurt dan volledig (niveau A):

| Stand in het plan | Wat de omvormer krijgt |
|---|---|
| Netladen | programma's op Grid, lading = doel uit het plan, programmavermogen en laadstroom uit het plan |
| Zelfverbruik | Zero Export To CT, ondergrens = je minimale lading, volle ontlaadstroom; de omvormer volgt zelf de meter |
| Vasthouden / alleen zon | ondergrens op de lading van nu; bij alleen zon ook netladen uit |
| Standby | programmavermogen en laad- en ontlaadstroom 0 |
| Netontladen | alleen als je *terugleveren toestaan* aanzet: Export First met het geplande vermogen |

**Je eigen instellingen blijven bewaard.** Voor de eerste opdracht bewaart de integratie je werkmodus,
programma's, stromen en exportgrens. Zet je de sturing uit, herlaad of verwijder je de integratie, of komt er
een pauze, noodstop of geen geldig plan, dan gaan die terug. Bij het afsluiten van Home Assistant niet: na de
herstart stuurt hij gewoon verder, zonder twee keer voor niets in het geheugen van de omvormer te schrijven.

**Zuinig schrijven.** Elke instelling hooguit één keer per vijf minuten, behalve bij een nieuwe stand; kleine
verschillen (2 % lading, 5 A, 250 W) worden niet geschreven. Volgorde: werkmodus, stromen, programma's. Elke
opdracht heeft 10 s; lukt het drie keer op rij niet, dan zie je een melding bij **Instellingen → Reparaties**
en gaan je eigen instellingen terug. Met fasestroomsensoren past de integratie de laadstroom stapsgewijs
(10 A per 10 s) aan je hoofdzekering aan.

**Terugkoppeling.** De sensor Energieplan laat zien wat het plan vraagt (`gevraagd_vermogen_w`), wat er is
toegepast (`toegepaste_stand`), waarom het eventueel minder is (`beperking`) en, bij een Deye, de laatste
opdracht aan de omvormer (`laatste_opdracht`).

## 4. Apparaten in het energieplan (optioneel, ook zonder batterij)

Vanaf 0.4.0 zet het energieplan ook je apparaten op het goedkoopste moment of op je eigen zon aan:
de auto, de boiler, de warmtepomp, een airco, elektrische verwarming, witgoed en een zwembadpomp.

1. Vink het apparaat aan bij het koppelen of onder **Configureren → Apparaten zoeken**, of voeg het
   toe in de webapp bij **Apparaten** (een schakelaar, thermostaat of boiler uit Home Assistant).
2. Kies het in de webapp bij **Energieplan → Instellen → Apparaten in het plan**, en vul in wanneer
   het klaar moet zijn. Onder *Meer instellingen* staan onder andere een prijsgrens, weekdagen, een
   minimale looptijd en comfortgrenzen voor de warmtepomp.
3. Zet de webapp op **Sturen** en vink in Home Assistant **Energieplan uitvoeren** aan. Een
   batterij-entiteit is hiervoor niet nodig.

**Veilig, voor elk merk.** Wat de integratie met een apparaat doet, volgt uit wat het kan, niet uit de merknaam:

- **Warmtepomp, airco, elektrische verwarming (`climate`).** Die gaan nooit uit. Zonneburen verschuift alleen
  de gewenste temperatuur: voorverwarmen +1 °C, zuiniger −0,5 °C (hooguit twee uur achter elkaar), binnen
  19–22 °C. `hvac_mode` blijft altijd zoals hij is. Uit mag alleen als je in de webapp *Aan en uit* kiest.
- **Boiler (`water_heater`).** Die gaat nooit uit en wordt nooit kouder dan je eigen temperatuur. Bij goedkope
  stroom of eigen zon warmt hij op tot 50 °C (60 °C voor legionella). Is het water al bijna zo warm, dan
  stookt hij niet extra.
- **Zelf veranderd?** Pas je de temperatuur zelf aan (op de thermostaat of in de app), dan wordt dat je nieuwe
  basis en laat Zonneburen het apparaat vier uur met rust.
- **Cloud-integraties.** Hooguit één schrijfactie per half uur per entiteit, en alleen bij een echte verandering.
  Meldt de integratie dat er vandaag minder dan 30 aanroepen over zijn, dan stopt de sturing tot morgen.
- **Wasmachine, vaatwasser, droger op een stekker.** De stekker gaat nooit uit terwijl het apparaat draait.
  Zet je programma klaar, druk op start en dan op **slim starten**. De stekker gaat uit tot het beste moment
  voor je eindtijd (**klaar om**). Dat moment is eerst je eigen zon, dan stroom over in de wijk, dan het
  goedkoopste tarief. Daarna loopt het programma in één keer door tot het klaar is, en blijft de stekker aan.
  Controleer of je machine na een stroomonderbreking verdergaat met het programma.
- **Terug naar slim regelen** zet niets meteen om: pas bij het volgende blok in het plan.
- **Stoppen, verwijderen, herladen, of meer dan 10 minuten geen verbinding.** Dan gaat alles terug naar
  je eigen temperatuur, en wat je verwijdert terug naar de stand van voor Zonneburen.

Bij een thermostaat of boiler zet het plan de temperatuur even hoger of lager; daarna gaat hij terug
naar de normale temperatuur uit Apparaten. Wat je zelf op *Handmatig* zet en de noodstop gaan altijd
voor. Bij een pauze of zonder geldig plan doen de apparaten weer wat ze normaal doen.

## 5. Het energieplan in Home Assistant bekijken (ook zonder internet)

Vanaf 0.5.0 staat er in de zijbalk een paneel **Energieplan**. Je hoeft er niets voor in te stellen.
Je ziet:

- wat je batterij de komende 36 uur doet, met de prijs per kwartier en de verwachte lading;
- wanneer elk apparaat aan gaat, met een baan per apparaat;
- per apparaat een tegel met zijn volgende blok.

Het plan staat in Home Assistant zelf. Valt het internet weg, dan voert Home Assistant het plan
gewoon uit tot het afloopt, en het paneel blijft het tonen.

Wil je het plan liever in je eigen dashboard? De kaarten zitten in de integratie en zijn meteen
beschikbaar, zonder resource toe te voegen:

```yaml
type: custom:zonneburen-energieplan-card
entity: sensor.zonneburen_de_kreekrug_9_energieplan
```

Per apparaat in het plan krijg je ook een sensor, bijvoorbeeld *Energieplan Laadpaal*, met als
waarde *Laden morgen 02:15–04:30*. Daar kun je automatiseringen of meldingen op bouwen.

Had je de kaart eerder als resource toegevoegd vanaf zonopzwaagdijk.nl? Die mag weg: de versie uit de
integratie werkt ook zonder internet.

## Bijwerken

Een nieuwe versie verschijnt vanzelf onder **Instellingen → Updates**. De webapp biedt voor Home
Assistant geen firmware aan.

## Problemen oplossen

| Wat zie je | Wat doe je |
| --- | --- |
| *Deze code is onbekend of verlopen* | Maak in de webapp een nieuwe koppelcode aan. |
| *Verbonden met Zonneburen* staat uit | Controleer je internet; de integratie verbindt zelf opnieuw. |
| Verbruik in de webapp is dubbel of raar | Zet de Zonneburen Link of de oude blueprint uit, en controleer het teken van je netsensor. |
| Batterij doet niets | Staat *Energieplan uitvoeren* aan en staat de webapp op *Sturen*? Kijk in de sensor *Energieplan*. |
| Batterij laadt als hij moet ontladen | Kies **Configureren → Thuisbatterij laten sturen**, klap *Details* open en vink *Positief getal betekent laden* aan of uit. |
| Meter of apparaat niet gevonden | Kies het zelf onder **Configureren**, of in de webapp bij *Apparaten*. |
| Geen logo te zien | Werk Home Assistant bij naar 2026.3 of nieuwer. |

Kom je er niet uit? Kies bij de integratie **⋮ → Diagnose downloaden** en stuur dat bestand mee met je
vraag via [GitHub](https://github.com/NiekSchouten/zonneburen-homeassistant/issues). Het wachtwoord
van de broker staat er niet in.

## Goed om te weten

- De verbinding met de wijk loopt over een eigen account op de broker van Zonneburen, met TLS. Dat
  staat los van een MQTT-broker die je zelf in Home Assistant gebruikt.
- Wat je in Home Assistant zelf schakelt, blijft staan. Alleen een wijkcommando, de noodstop of een
  keuze in de webapp verandert iets.
- Witgoed, eigen zon eerst en een prijsgrens stel je per apparaat in bij het energieplan in de webapp
  (zie hierboven). De losse regels van de hub voor piekuren komen in een volgende versie.
