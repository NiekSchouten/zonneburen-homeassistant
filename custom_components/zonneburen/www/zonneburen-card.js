/*
 * ☀️ Zonneburen Card v2.0.0
 * De kaarten van Zonneburen voor Home Assistant. De integratie levert dit bestand zelf mee en registreert
 * ook het paneel Energieplan in de zijbalk. Elke kaart vindt zijn sensor zelf en heeft een visuele editor:
 *   custom:zonneburen-batterij-card       je batterij nu, met knoppen
 *   custom:zonneburen-energieplan-card    het plan op een tijdlijn
 *   custom:zonneburen-kwartier-card       het schema per kwartier, zoals in de webapp
 *   custom:zonneburen-apparaten-card      wanneer het plan je apparaten laat draaien
 *   custom:zonneburen-buurtbatterij-card  de virtuele buurtbatterij
 *   custom:zonneburen-card                de wijk als straat met huisjes (hieronder)
 *
 * Visualiseert de hele wijk als een rij Hollandse huisjes:
 *   - amber + gloed       = huis deelt overschot met de wijk
 *   - blauw                = huis buffert wijkstroom (neemt af)
 *   - paars, knipperend    = wacht op akkoord van de bewoner (pushbericht verstuurd)
 *   - grijs, stippellijn   = geblokkeerd door piekuren/hoog tarief/batterijprioriteit
 *   - contour              = neutraal
 *   - grijs, gedimd        = offline
 * Jouw eigen huis krijgt een ring + label. 's Nachts (via sun.sun) doven de huisjes en
 * krijgen ze verlichte raampjes; overdag gloeit er een zonnetje mee met de echte
 * zonnestand én het actuele wijk-overschot. Zodra er zowel delende als bufferende huizen
 * zijn, lopen er geanimeerde stroom-stipjes van de een naar de ander over de straatlijn.
 *
 * Verwacht één sensor (via MQTT Discovery vanaf de wijk-server), bijv:
 *   state: netto wijk-overschot in Watt (positief = wijk levert terug)
 *   attributes:
 *     noodstop: false
 *     beursprijs: 0.24
 *     online: 14
 *     totaal: 16
 *     ranglijst: { maand: "juli 2026", zonnekoning: "De Kreekrug 9", sponsje: "Dorpsweg 2", ijzervreter: "Dorpsweg 4" }
 *     huizen:
 *       - { adres: "De Kreekrug 9", status: "deelt", watt: 1240, soc: 85,
 *           boiler_watt: 900, ev_watt: 0, airco_watt: 0, overige_watt: 0,
 *           vandaag_gedeeld_kwh: 3.2, vandaag_ontvangen_kwh: 1.1, vandaag_bespaard_euro: 0.84 }
 *       - { adres: "De Kreekrug 11", status: "neemt_af", watt: -600 }
 *       - { adres: "Dorpsweg 2", status: "wacht_op_bewoner", watt: 0 }
 *       - { adres: "Dorpsweg 4", status: "geblokkeerd", watt: 30 }
 *       - { adres: "Dorpsweg 6", status: "neutraal", watt: 0 }
 *       - { adres: "Dorpsweg 8", status: "offline" }
 *
 * Tweede kaart in dit bestand: het Energieplan van je eigen huis, uit de Zonneburen-integratie.
 *   type: custom:zonneburen-energieplan-card
 *   entity: sensor.zonneburen_de_kreekrug_9_energieplan
 *   apparaten: true               # optioneel: false verbergt de lijst met apparaten
 * De integratie (vanaf 0.5.0) levert dit bestand zelf mee en registreert ook het paneel
 * Energieplan in de zijbalk (zonneburen-energieplan-panel).
 *
 * Config (YAML):
 *   type: custom:zonneburen-card
 *   entity: sensor.zonneburen_wijk
 *   mijn_adres: "De Kreekrug 9"   # optioneel: markeert jouw huis + toont je persoonlijke teller
 *   titel: "Zonneburen"           # optioneel
 *   compact: false                # optioneel: verbergt legenda + detailstrip + teller
 */

(function () {
  "use strict";

  const VERSIE = "2.0.0";
  // De integratie levert dit bestand zelf mee; staat het ook als resource in een dashboard, dan
  // laadt het twee keer. De eerste wint.
  if (customElements.get("zonneburen-card")) return;

  const STATUS = {
    deelt:            { label: "Deelt met de wijk",              kleur: "var(--zb-deelt)" },
    neemt_af:         { label: "Buffert wijkstroom",             kleur: "var(--zb-afname)" },
    wacht_op_bewoner: { label: "Wacht op akkoord bewoner",        kleur: "var(--zb-wacht)" },
    geblokkeerd:      { label: "Geblokkeerd (piek/prijs/accu)",   kleur: "var(--zb-geblokkeerd)" },
    neutraal:         { label: "Neutraal",                       kleur: "var(--zb-neutraal)" },
    offline:          { label: "Offline",                        kleur: "var(--zb-offline)" },
  };

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const fmtWatt = (w) => {
    if (w === null || w === undefined || isNaN(w)) return "–";
    const abs = Math.abs(w);
    if (abs >= 1000) return (w / 1000).toFixed(1).replace(".", ",") + " kW";
    return Math.round(w) + " W";
  };

  const fmtKwh = (kwh) => {
    const n = Number(kwh);
    if (!isFinite(n)) return "0 kWh";
    return n.toFixed(1).replace(".", ",") + " kWh";
  };

  const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const fmtGeleden = (isoTijdstip) => {
    if (!isoTijdstip) return "";
    const sec = Math.max(0, Math.round((Date.now() - new Date(isoTijdstip).getTime()) / 1000));
    if (sec < 90) return "zojuist bijgewerkt";
    const min = Math.round(sec / 60);
    if (min < 60) return `${min} min. geleden bijgewerkt`;
    return `${Math.round(min / 60)} uur geleden bijgewerkt`;
  };

  class ZonneburenCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._laatsteRender = null;
      this._geselecteerd = null;
      this.shadowRoot.addEventListener("click", (ev) => this._opKlik(ev));
      this.shadowRoot.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          const huis = ev.target.closest("[data-adres]");
          if (huis) { ev.preventDefault(); this._selecteer(huis.dataset.adres); }
        }
      });
    }

    // Zonder entity zoekt de kaart zelf de wijk-sensor (van de integratie, of via MQTT Discovery).
    setConfig(config) {
      this._config = {
        titel: "Zonneburen",
        compact: false,
        mijn_adres: null,
        ...config,
      };
      this._laatsteRender = null;
    }

    static getStubConfig(hass) {
      return { entity: zoek(hass, "wijk") || "" };
    }

    static getConfigElement() {
      const e = document.createElement("zonneburen-kaart-editor");
      e.schema = [
        { name: "entity", label: "Wijk-sensor", selector: { entity: { domain: "sensor" } } },
        { name: "titel", label: "Titel", selector: { text: {} } },
        { name: "mijn_adres", label: "Mijn adres (leeg = vanzelf)", selector: { text: {} } },
        { name: "compact", label: "Compact", selector: { boolean: {} } },
      ];
      return e;
    }

    getCardSize() {
      return this._config && this._config.compact ? 3 : 5;
    }

    set hass(hass) {
      this._hass = hass;
      this._id = this._config.entity || zoek(hass, "wijk");
      const st = this._id ? hass.states[this._id] : undefined;
      // Alleen opnieuw tekenen als er echt iets veranderd is.
      const vingerafdruk = st
        ? st.last_updated + "|" + st.state + "|" + (this._geselecteerd || "")
        : "leeg|" + (this._geselecteerd || "");
      if (vingerafdruk === this._laatsteRender) return;
      this._laatsteRender = vingerafdruk;
      this._render(st);
    }

    _opKlik(ev) {
      const huis = ev.target.closest("[data-adres]");
      if (huis) { this._selecteer(huis.dataset.adres); return; }
      if (ev.target.closest("[data-actie='meer-info']")) {
        this.dispatchEvent(new CustomEvent("hass-more-info", {
          bubbles: true, composed: true,
          detail: { entityId: this._id },
        }));
      }
    }

    _selecteer(adres) {
      this._geselecteerd = this._geselecteerd === adres ? null : adres;
      this._laatsteRender = null;
      if (this._hass) this.hass = this._hass;
    }

    /* ---------- SVG: Hollands huisje met puntgevel ---------- */
    _huisSvg(status, isMij, isNacht) {
      const s = STATUS[status] || STATUS.neutraal;
      const dicht = status === "deelt" || status === "neemt_af";
      const gestippeld = status === "geblokkeerd";
      // 's Nachts krijgt elk huis (behalve offline: daar is 'niemand thuis') verlichte
      // raampjes — een klein detail dat de straat meteen levend laat aanvoelen.
      const ramenAan = isNacht && status !== "offline";
      return `
        <svg viewBox="0 0 44 44" aria-hidden="true">
          ${isMij ? `<circle cx="22" cy="23" r="20.5" class="ring"/>` : ""}
          <path d="M8 21 L22 8 L36 21 V37 H8 Z"
                fill="${dicht ? s.kleur : (isNacht ? "var(--zb-nacht-romp)" : "none")}"
                stroke="${status === "neutraal" ? "var(--zb-contour)" : s.kleur}"
                stroke-width="2.4" stroke-linejoin="round"
                stroke-dasharray="${gestippeld ? "3 3" : "0"}"
                class="romp ${status === "deelt" ? "pulseer" : ""} ${status === "wacht_op_bewoner" ? "knipper" : ""}"/>
          <rect x="27" y="10" width="3.6" height="7" rx="0.8"
                fill="${status === "neutraal" ? "var(--zb-contour)" : s.kleur}" class="schoorsteen"/>
          ${ramenAan ? `
            <rect x="12.5" y="24" width="5.5" height="5.5" rx="0.8" class="raam"/>
            <rect x="26" y="24" width="5.5" height="5.5" rx="0.8" class="raam"/>` : ""}
          <rect x="18.5" y="26" width="7" height="11" rx="1"
                fill="${dicht ? "var(--ha-card-background, var(--card-background-color, #fff))" : "transparent"}"
                stroke="${status === "neutraal" ? "var(--zb-contour)" : s.kleur}"
                stroke-width="1.6" class="deur"/>
          ${status === "geblokkeerd" ? `<text x="22" y="26" text-anchor="middle" font-size="9">⏸</text>` : ""}
        </svg>`;
    }

    _render(st) {
      const cfg = this._config;

      /* ---------- Foutstaten ---------- */
      if (!st) {
        this.shadowRoot.innerHTML = `
          ${this._stijlen()}
          <ha-card>
            <div class="melding">
              <span class="melding-titel">Nog geen wijkgegevens</span>
              <span>${cfg.entity ? `"${esc(cfg.entity)}" bestaat (nog) niet.` : "Er is nog geen wijk-sensor."} Met de Zonneburen-integratie
              verschijnt de sensor Overschot in de wijk vanzelf zodra Home Assistant verbonden is.</span>
            </div>
          </ha-card>`;
        return;
      }

      const attrs = st.attributes || {};
      const huizen = Array.isArray(attrs.huizen) ? attrs.huizen : [];
      const netto = parseFloat(st.state);
      const noodstop = attrs.noodstop === true;
      const online = attrs.online, totaal = attrs.totaal;
      const beursprijs = attrs.beursprijs;
      const ranglijst = attrs.ranglijst || {};

      const richting = isNaN(netto) ? null : netto > 50 ? "over" : netto < -50 ? "tekort" : "balans";
      const richtingTekst = {
        over: "wijk-overschot",
        tekort: "wijk-tekort",
        balans: "in balans",
      }[richting] || "wijkstatus";

      const geselecteerd = huizen.find((h) => h && h.adres === this._geselecteerd);

      /* ---------- Dag/nacht-sfeer, op basis van de echte zonnestand (sun.sun) ---------- */
      const sunState = this._hass && this._hass.states && this._hass.states["sun.sun"];
      const isNacht = sunState ? sunState.state !== "above_horizon" : false;
      const zonElevatie = sunState && sunState.attributes && typeof sunState.attributes.elevation === "number"
        ? Math.max(0, Math.min(60, sunState.attributes.elevation)) : 30;
      // Het zonnetje gloeit feller naarmate de wijk daadwerkelijk meer overschot heeft —
      // niet puur decoratief, maar een directe weerspiegeling van de live productie.
      const zonGloeiIntensiteit = !isNaN(netto) && netto > 0 ? Math.max(0.25, Math.min(1, netto / 3000)) : 0.25;

      /* ---------- Persoonlijke teller: jouw eigen huis van vandaag ---------- */
      // Je eigen huis: uit de kaart, of zoals de integratie het meldt.
      const mijnAdres = cfg.mijn_adres || attrs.mijn_adres || null;
      const mijnHuis = mijnAdres ? huizen.find((h) => h && h.adres === mijnAdres) : null;
      const mijnTeller = !cfg.compact && mijnHuis && mijnHuis.vandaag_gedeeld_kwh !== undefined ? `
        <div class="mijn-teller">
          Vandaag <b>${fmtKwh(mijnHuis.vandaag_gedeeld_kwh)}</b> gedeeld
          &middot; <b>${fmtKwh(mijnHuis.vandaag_ontvangen_kwh)}</b> ontvangen
          &middot; &asymp; <b>€ ${Number(mijnHuis.vandaag_bespaard_euro || 0).toFixed(2)}</b> bespaard
        </div>` : "";

      /* ---------- Huizenrij ---------- */
      const straat = huizen.length
        ? huizen.map((h) => {
            const status = STATUS[h.status] ? h.status : "neutraal";
            const isMij = mijnAdres && h.adres === mijnAdres;
            const actief = this._geselecteerd === h.adres;
            return `
              <div class="huis ${status} ${actief ? "actief" : ""}" tabindex="0" role="button"
                   data-adres="${esc(h.adres)}"
                   aria-label="${esc(h.adres)}: ${STATUS[status].label}">
                ${this._huisSvg(status, isMij, isNacht)}
                ${isMij ? `<span class="mij-label">JIJ</span>` : ""}
                ${ranglijst.zonnekoning === h.adres ? `<span class="badge" title="Zonnekoning: meest gedeeld deze maand">👑</span>` : ""}
                ${ranglijst.sponsje === h.adres ? `<span class="badge badge-2" title="Sponsje: meest gebufferd deze maand">🧽</span>` : ""}
                ${ranglijst.ijzervreter === h.adres ? `<span class="badge badge-3" title="IJzervreter: nooit offline deze maand">🔧</span>` : ""}
                <span class="adres">${esc(h.adres)}</span>
              </div>`;
          }).join("")
        : `<div class="leeg">Nog geen huizen ontvangen van de wijk-server.
           Zodra er telemetrie binnenkomt verschijnt hier de straat.</div>`;

      /* ---------- Detailstrip ---------- */
      let detail = "";
      if (!cfg.compact && geselecteerd) {
        const s = STATUS[geselecteerd.status] ? geselecteerd.status : "neutraal";
        const w = geselecteerd.watt;
        const soc = geselecteerd.soc;

        const apparaten = [
          ["Boiler", geselecteerd.boiler_watt],
          ["Laadpaal", geselecteerd.ev_watt],
          ["Airco", geselecteerd.airco_watt],
          ["Overig", geselecteerd.overige_watt],
        ].filter(([, watt]) => typeof watt === "number" && watt > 0);

        detail = `
          <div class="detail" style="--accent:${STATUS[s].kleur}">
            <div class="detail-top">
              <span class="detail-adres">${esc(geselecteerd.adres)}</span>
              <span class="detail-status">${STATUS[s].label}</span>
              <span class="detail-meta">
                ${w !== undefined && w !== null ? `<b>${fmtWatt(Math.abs(w))}</b>` : ""}
                ${soc !== undefined && soc !== null ? ` · accu <b>${esc(soc)}%</b>` : ""}
              </span>
            </div>
            ${apparaten.length ? `
              <div class="detail-apparaten">
                ${apparaten.map(([naam, watt]) => `<span class="detail-chip">${naam}: <b>${fmtWatt(watt)}</b></span>`).join("")}
              </div>` : ""}
          </div>`;
      }

      /* ---------- Legenda ---------- */
      const legenda = cfg.compact ? "" : `
        <div class="legenda">
          ${Object.entries(STATUS).map(([k, v]) => `
            <span class="legenda-item"><i style="background:${k === "neutraal" ? "transparent" : v.kleur};
              ${k === "neutraal" ? "border:2px solid var(--zb-contour);" : ""}
              ${k === "geblokkeerd" ? "border:2px dashed var(--zb-geblokkeerd); background:transparent;" : ""}"></i>${v.label}</span>`).join("")}
        </div>`;

      /* ---------- Opstart-melding: nog maar 0 of 1 huis zichtbaar ---------- */
      // Dit is géén foutstatus (de sensor werkt prima), maar een veelvoorkomend moment
      // vlak na de lancering: nog niet iedereen heeft een hardware-token + 'zichtbaar op de
      // wijkkaart' aanstaan. Zonder deze melding lijkt de kaart dan defect.
      const opstartMelding = !cfg.compact && huizen.length <= 1 ? `
        <div class="opstart" role="status">
          👋 ${huizen.length === 0 ? "Nog geen huizen bekend bij de wijk-server." : "Nog maar 1 huis zichtbaar in de wijk."}
          Vraag buren om bij "Mijn Gegevens" hun hardware-token in te vullen en
          "Zichtbaar op de wijkkaart" aan te zetten &mdash; ze verschijnen dan vanzelf hier.
        </div>` : "";

      this.shadowRoot.innerHTML = `
        ${this._stijlen()}
        <ha-card class="${isNacht ? "zb-nacht" : ""}">
          ${noodstop ? `<div class="noodstop" role="alert">🛑 Wijkbrede noodstop actief — alle apparaten terug naar normaal bedrijf</div>` : ""}
          ${opstartMelding}
          <div class="kop">
            ${!isNacht ? `<div class="zb-zon" style="--elevatie:${zonElevatie}; --gloei:${zonGloeiIntensiteit};" aria-hidden="true"></div>` : ""}
            <div class="kop-links">
              <span class="eyebrow">${esc(cfg.titel)}</span>
              <span class="netto ${richting || ""}" data-actie="meer-info" title="Open sensordetails">
                ${isNaN(netto) ? "–" : fmtWatt(Math.abs(netto))}
              </span>
              <span class="netto-label">${richtingTekst}${beursprijs !== undefined && beursprijs !== null ? ` · € ${Number(beursprijs).toFixed(2)}/kWh` : ""}</span>
              ${mijnTeller}
            </div>
            <div class="kop-rechts">
              ${online !== undefined && totaal !== undefined
                ? `<span class="chip"><i class="stip"></i>${esc(online)}/${esc(totaal)} online</span>` : ""}
              <span class="bijgewerkt">${fmtGeleden(st.last_updated)}</span>
            </div>
          </div>
          <div class="straat">${straat}</div>
          ${detail}
          ${legenda}
        </ha-card>`;

      if (!REDUCED_MOTION) {
        requestAnimationFrame(() => this._tekenEnergiestromen());
      }
    }

    /* ---------- Geanimeerde energiestromen: stipjes van delende naar bufferende huizen ---------- */
    _tekenEnergiestromen() {
      const straatEl = this.shadowRoot.querySelector(".straat");
      if (!straatEl) return;

      const producenten = [...straatEl.querySelectorAll(".huis.deelt")];
      const consumenten = [...straatEl.querySelectorAll(".huis.neemt_af")];
      if (producenten.length === 0 || consumenten.length === 0) return;

      const straatRect = straatEl.getBoundingClientRect();
      const middenX = (el) => el.getBoundingClientRect().left + el.getBoundingClientRect().width / 2 - straatRect.left;
      const vanPosities = producenten.map(middenX);
      const naarPosities = consumenten.map(middenX);

      const overlay = document.createElement("div");
      overlay.className = "zb-stroom-overlay";
      const aantalStippen = Math.min(6, Math.max(vanPosities.length, naarPosities.length) * 2);

      for (let i = 0; i < aantalStippen; i++) {
        const stip = document.createElement("span");
        stip.className = "zb-stip";
        stip.style.setProperty("--van", `${vanPosities[i % vanPosities.length]}px`);
        stip.style.setProperty("--naar", `${naarPosities[i % naarPosities.length]}px`);
        stip.style.animationDelay = `${(i / aantalStippen) * 2.4}s`;
        overlay.appendChild(stip);
      }

      straatEl.appendChild(overlay);
    }

    _stijlen() {
      return `<style>
        :host {
          --zb-deelt: #FFB233;
          --zb-afname: #4AA8DE;
          --zb-wacht: #A78BFA;
          --zb-geblokkeerd: #94A3B8;
          --zb-offline: #8A919C;
          --zb-neutraal: var(--secondary-text-color, #727272);
          --zb-contour: var(--divider-color, #3a3f47);
          --zb-nood: #E5484D;
          --zb-nacht-romp: #1e2536;
          --zb-raam-gloei: #FFD873;
        }
        ha-card { overflow: hidden; transition: background-color 0.6s ease; }
        ha-card.zb-nacht { background: color-mix(in srgb, #0b1020 22%, var(--ha-card-background, var(--card-background-color, #fff))); }

        .noodstop {
          background: var(--zb-nood);
          color: #fff;
          font-weight: 600;
          font-size: 0.85rem;
          letter-spacing: 0.01em;
          padding: 8px 16px;
        }

        .opstart {
          background: color-mix(in srgb, var(--zb-afname) 14%, transparent);
          color: var(--primary-text-color);
          font-size: 0.8rem;
          line-height: 1.4;
          padding: 10px 16px;
          border-bottom: 1px solid var(--zb-contour);
        }

        .kop {
          position: relative;
          display: flex; align-items: flex-start; justify-content: space-between;
          gap: 8px; flex-wrap: wrap;
          padding: 16px 16px 4px;
          overflow: hidden;
        }
        .kop-rechts {
          display: flex; flex-direction: column; align-items: flex-end; gap: 4px;
        }
        .zb-zon {
          position: absolute;
          top: calc(38px - var(--elevatie, 30) * 0.55px);
          right: 18px;
          width: 34px; height: 34px;
          border-radius: 50%;
          background: radial-gradient(circle, #FFD873 0%, #FFB233 60%, transparent 72%);
          opacity: calc(0.35 + var(--gloei, 0.5) * 0.65);
          filter: blur(0.4px);
          box-shadow: 0 0 calc(10px + var(--gloei, 0.5) * 26px) calc(1px + var(--gloei, 0.5) * 6px) rgba(255, 178, 51, 0.55);
          transition: top 1.2s ease, opacity 1.2s ease, box-shadow 1.2s ease;
          pointer-events: none;
          z-index: 0;
        }
        .mijn-teller {
          margin-top: 6px;
          font-size: 0.78rem;
          color: var(--secondary-text-color);
        }
        .mijn-teller b { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
        .eyebrow {
          display: block;
          font-size: 0.7rem; font-weight: 700;
          letter-spacing: 0.14em; text-transform: uppercase;
          color: var(--secondary-text-color);
          margin-bottom: 2px;
        }
        .netto {
          font-size: 2rem; font-weight: 700; line-height: 1.1;
          font-variant-numeric: tabular-nums;
          cursor: pointer;
          color: var(--primary-text-color);
        }
        .netto.over   { color: var(--zb-deelt); }
        .netto.tekort { color: var(--zb-afname); }
        .netto-label {
          display: block;
          font-size: 0.8rem;
          color: var(--secondary-text-color);
        }
        .chip {
          display: inline-flex; align-items: center; gap: 6px;
          font-size: 0.75rem; font-weight: 600;
          color: var(--secondary-text-color);
          border: 1px solid var(--zb-contour);
          border-radius: 999px;
          padding: 4px 10px;
          white-space: nowrap;
        }
        .stip {
          width: 7px; height: 7px; border-radius: 50%;
          background: #3FB68B; display: inline-block;
        }
        .bijgewerkt {
          font-size: 0.65rem;
          color: var(--secondary-text-color);
          opacity: 0.75;
          white-space: nowrap;
        }

        .straat {
          position: relative;
          display: flex; flex-wrap: wrap; gap: 4px 2px;
          align-items: flex-end;
          padding: 10px 12px 4px;
          border-bottom: 2px solid var(--zb-contour); /* het 'straatje' */
          margin: 0 16px;
        }
        .huis {
          width: 58px;
          display: flex; flex-direction: column; align-items: center;
          padding: 4px 0 6px;
          border-radius: 8px;
          cursor: pointer;
          position: relative;
          outline: none;
        }
        .huis:focus-visible { box-shadow: 0 0 0 2px var(--zb-afname); }
        .huis.actief { background: color-mix(in srgb, var(--zb-contour) 35%, transparent); }
        .huis svg { width: 44px; height: 44px; display: block; }
        .huis.offline { opacity: 0.45; }
        .huis.geblokkeerd { opacity: 0.85; }
        .ring {
          fill: none;
          stroke: var(--primary-text-color);
          stroke-width: 1.6;
          stroke-dasharray: 3 3;
          opacity: 0.75;
        }
        .mij-label {
          position: absolute; top: -2px; right: 4px;
          font-size: 0.55rem; font-weight: 800; letter-spacing: 0.08em;
          color: var(--primary-text-color);
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border: 1px solid var(--zb-contour);
          border-radius: 4px; padding: 0 3px;
        }
        .adres {
          margin-top: 2px;
          max-width: 56px;
          font-size: 0.6rem;
          color: var(--secondary-text-color);
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          text-align: center;
        }
        .raam {
          fill: var(--zb-raam-gloei);
          opacity: 0.95;
          filter: drop-shadow(0 0 2px var(--zb-raam-gloei));
        }
        .badge {
          position: absolute; top: -4px; left: 2px;
          font-size: 0.7rem; line-height: 1;
        }
        .badge-2 { left: auto; right: 2px; top: -4px; }
        .badge-3 { left: 50%; top: -8px; transform: translateX(-50%); }

        .zb-stroom-overlay { position: absolute; inset: 0; pointer-events: none; overflow: visible; }
        .zb-stip {
          position: absolute;
          bottom: 1px;
          left: 0;
          width: 6px; height: 6px; border-radius: 50%;
          background: var(--zb-deelt);
          box-shadow: 0 0 6px var(--zb-deelt);
          animation: zb-stroom 2.4s linear infinite;
        }
        @keyframes zb-stroom {
          0%   { transform: translateX(var(--van)) scale(0.4); opacity: 0; }
          8%   { opacity: 1; transform: translateX(var(--van)) scale(1); }
          92%  { opacity: 1; }
          100% { transform: translateX(var(--naar)) scale(0.4); opacity: 0; }
        }

        @keyframes zb-gloei {
          0%, 100% { filter: drop-shadow(0 0 1px var(--zb-deelt)); }
          50%      { filter: drop-shadow(0 0 7px var(--zb-deelt)); }
        }
        .pulseer { animation: zb-gloei 2.6s ease-in-out infinite; }

        @keyframes zb-knipper {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.35; }
        }
        .knipper { animation: zb-knipper 1.1s ease-in-out infinite; }

        @media (prefers-reduced-motion: reduce) {
          .pulseer { animation: none; filter: drop-shadow(0 0 3px var(--zb-deelt)); }
          .knipper { animation: none; opacity: 1; }
          .zb-stip { animation: none; display: none; }
        }

        .detail {
          margin: 10px 16px 0;
          padding: 8px 12px;
          border-left: 3px solid var(--accent);
          background: color-mix(in srgb, var(--accent) 10%, transparent);
          border-radius: 0 8px 8px 0;
          font-size: 0.85rem;
        }
        .detail-top {
          display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
        }
        .detail-adres { font-weight: 700; }
        .detail-status { color: var(--secondary-text-color); }
        .detail-meta b { font-variant-numeric: tabular-nums; }
        .detail-apparaten {
          display: flex; flex-wrap: wrap; gap: 6px 14px;
          margin-top: 6px;
          font-size: 0.75rem;
          color: var(--secondary-text-color);
        }
        .detail-chip b { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }

        .legenda {
          display: flex; flex-wrap: wrap; gap: 6px 16px;
          padding: 12px 16px 14px;
          font-size: 0.72rem;
          color: var(--secondary-text-color);
        }
        .legenda-item { display: inline-flex; align-items: center; gap: 6px; }
        .legenda-item i {
          width: 10px; height: 10px; border-radius: 3px; display: inline-block;
        }

        .leeg, .melding {
          padding: 20px 16px;
          font-size: 0.85rem;
          color: var(--secondary-text-color);
          line-height: 1.5;
        }
        .melding-titel {
          display: block; font-weight: 700;
          color: var(--primary-text-color);
          margin-bottom: 4px;
        }
      </style>`;
    }
  }

  customElements.define("zonneburen-card", ZonneburenCard);

  // ------------------------------------------------------------------ Gedeeld voor de kaarten van de integratie

  const PLAN_ZIN = {
    zelfverbruik: "Eigen stroom gebruiken", netladen: "Goedkoop bijladen van het net",
    netontladen: "Stroom uit de batterij terugleveren", vasthouden: "Lading bewaren voor later",
    alleenzon: "Alleen zon opslaan", standby: "Batterij rust",
  };
  // Dezelfde kleuren als de webapp: blauw bijladen, groen batterij, oranje zon, paars vasthouden.
  const KLEUR = { zon: "#f59e0b", groen: "#059669", lucht: "#0284c7", violet: "#7c3aed", rood: "#dc2626", grijs: "#94a3b8" };
  const PLAN_KLEUR = {
    netladen: KLEUR.lucht, netontladen: KLEUR.groen, zelfverbruik: KLEUR.grijs,
    vasthouden: KLEUR.violet, alleenzon: KLEUR.zon, standby: KLEUR.grijs,
  };
  const PLAN_ICOON = {
    netladen: "mdi:transmission-tower-import", netontladen: "mdi:transmission-tower-export", zelfverbruik: "mdi:home-lightning-bolt",
    vasthouden: "mdi:battery-lock", alleenzon: "mdi:solar-power-variant", standby: "mdi:battery-outline",
  };
  const KWARTIER = 900;

  const tijd = (unix) => new Date(unix * 1000).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
  const euro = (v) => "€ " + Number(v).toFixed(2).replace(".", ",");
  const euro3 = (v) => (typeof v === "number" ? "€ " + v.toFixed(3).replace(".", ",") : "–");
  const kwh = (v) => Number(v).toFixed(Math.abs(v) < 10 ? 1 : 0).replace(".", ",") + " kWh";
  const procent = (v) => Math.round(v) + "%";
  const dagNummer = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dagenVanaf = (unix) => Math.round((dagNummer(new Date(unix * 1000)) - dagNummer(new Date())) / 864e5);
  // "02:15", of "morgen 02:15" als het niet meer vandaag is.
  const dagTijd = (unix) => {
    const dagen = dagenVanaf(unix);
    return (dagen === 1 ? "morgen " : dagen > 1 ? "overmorgen " : "") + tijd(unix);
  };
  const dagKop = (unix) => {
    const dagen = dagenVanaf(unix);
    return dagen === 0 ? "Vandaag" : dagen === 1 ? "Morgen" : new Date(unix * 1000).toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" });
  };
  const klem = (v, a, b) => Math.min(b, Math.max(a, v));

  // De sensoren van de integratie dragen een attribuut "zonneburen" met hun soort. Oudere versies niet:
  // dan herkennen we ze aan hun attributen.
  const SOORT = {
    energieplan: (s) => s.attributes.zonneburen === "energieplan" || Array.isArray(s.attributes.kwartieren),
    wijk: (s) => s.attributes.zonneburen === "wijk" || Array.isArray(s.attributes.huizen),
    buurtbatterij: (s) => s.attributes.zonneburen === "buurtbatterij",
  };
  const zoek = (hass, soort) => {
    if (!hass || !hass.states || !SOORT[soort]) return undefined;
    return Object.keys(hass.states).filter((id) => id.startsWith("sensor.")).sort().find((id) => SOORT[soort](hass.states[id]));
  };
  // Een andere entiteit van hetzelfde huis: via het apparaat in de registry, anders op naam.
  const broer = (hass, id, sleutel, domein) => {
    if (!hass || !id) return undefined;
    const reg = hass.entities || {};
    const eigen = reg[id];
    if (eigen && eigen.device_id) {
      const gevonden = Object.values(reg).find((e) => e.device_id === eigen.device_id && e.translation_key === sleutel && e.entity_id.startsWith(domein + "."));
      if (gevonden) return gevonden.entity_id;
    }
    const basis = id.replace(/^sensor\./, "").replace(/_(energieplan|overschot_in_de_wijk|wijk_overschot)$/, "");
    const naam = `${domein}.${basis}_${sleutel}`;
    return hass.states[naam] ? naam : undefined;
  };

  // De basis voor elke kaart: vindt zelf zijn sensor, heeft een editor, en tekent alleen opnieuw als er iets
  // veranderde (of elke minuut, zodat "nu" meeloopt).
  class ZbKaart extends HTMLElement {
    static get soort() { return "energieplan"; }
    static get schema() { return [{ name: "entity", label: "Sensor", selector: { entity: { integration: "zonneburen", domain: "sensor" } } }]; }
    static getConfigElement() {
      const e = document.createElement("zonneburen-kaart-editor");
      e.schema = this.schema;
      return e;
    }
    static getStubConfig(hass) { return { entity: zoek(hass, this.soort) || "" }; }

    setConfig(config) {
      this._config = { ...(config || {}) };
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.addEventListener("click", (ev) => this._klik(ev));
      }
      this._vorige = null;
      if (this._hass) this.hass = this._hass;
    }

    getCardSize() { return 4; }

    connectedCallback() {
      this._tik = setInterval(() => { this._vorige = null; if (this._hass) this.hass = this._hass; }, 60000);
    }
    disconnectedCallback() { clearInterval(this._tik); }

    get _entiteit() { return (this._config && this._config.entity) || zoek(this._hass, this.constructor.soort); }

    set hass(hass) {
      this._hass = hass;
      if (!this._config) return;
      const id = this._entiteit;
      const st = id ? hass.states[id] : undefined;
      const sleutel = (st ? st.last_updated + "|" + st.state : "leeg") + "|" + this._extraSleutel(hass, id);
      if (sleutel === this._vorige) return;
      this._vorige = sleutel;
      this._id = id;
      this.shadowRoot.innerHTML = (st ? this._inhoud(st, hass) : this._leeg()) + `<style>${BASIS_STIJL}${this._stijl()}</style>`;
    }

    _extraSleutel() { return ""; }
    _stijl() { return ""; }
    _klik() {}
    _leeg() {
      return `<ha-card><div class="zb-leeg"><ha-icon icon="mdi:solar-power-variant"></ha-icon><div><b>Nog geen gegevens van Zonneburen</b>
        <span>Deze kaart vindt de sensor van de Zonneburen-integratie vanzelf zodra die er is. Staat de integratie al?
        Zet in de webapp bij Energieplan de stand op Adviseren of Sturen; het plan komt dan binnen enkele minuten binnen.</span></div></div></ha-card>`;
    }
    _toonMeer(id) {
      this.dispatchEvent(new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId: id || this._id } }));
    }
  }

  const BASIS_STIJL = `
    :host { --zb-zon: ${KLEUR.zon}; --zb-groen: ${KLEUR.groen}; --zb-lucht: ${KLEUR.lucht}; --zb-violet: ${KLEUR.violet}; --zb-rood: ${KLEUR.rood};
            --zb-zacht: var(--secondary-background-color, rgba(127,127,127,.1)); --zb-lijn: var(--divider-color, rgba(127,127,127,.2)); }
    ha-card { padding: 16px; container-type: inline-size; overflow: hidden; }
    .zb-kop { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
    .zb-titel { font-size: 1.1rem; font-weight: 700; color: var(--primary-text-color); line-height: 1.3; }
    .zb-sub { color: var(--secondary-text-color); font-size: .85rem; margin-top: 2px; }
    .zb-leeg { display: flex; gap: 14px; align-items: flex-start; color: var(--secondary-text-color); line-height: 1.5; }
    .zb-leeg ha-icon { --mdc-icon-size: 32px; color: var(--zb-zon); flex: none; }
    .zb-leeg b { display: block; color: var(--primary-text-color); }
    .zb-chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 99px; font-size: .78rem; font-weight: 600;
               background: var(--zb-zacht); color: var(--primary-text-color); white-space: nowrap; }
    .zb-chip.goed { background: rgba(5,150,105,.14); color: var(--zb-groen); }
    .zb-chip.let-op { background: rgba(245,158,11,.16); color: #b45309; }
    .zb-chip.fout { background: rgba(220,38,38,.12); color: var(--zb-rood); }
    .zb-segment { display: inline-flex; gap: 2px; padding: 3px; border-radius: 10px; background: var(--zb-zacht); max-width: 100%; overflow-x: auto; }
    .zb-segment button { border: 0; background: none; padding: 5px 11px; border-radius: 8px; font: inherit; font-size: .8rem; font-weight: 600;
                         color: var(--secondary-text-color); cursor: pointer; white-space: nowrap; }
    .zb-segment button.aan { background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 1px 3px rgba(0,0,0,.15); }
    .zb-melding { margin: 8px 0; padding: 8px 12px; border-radius: 10px; background: rgba(220,38,38,.1); font-size: .85rem; color: var(--primary-text-color); }
    .zb-melding.info { background: rgba(2,132,199,.1); }
    h3.zb-h { margin: 16px 0 8px; font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); }
    .zb-voet { margin-top: 12px; font-size: .78rem; color: var(--secondary-text-color); line-height: 1.45; }
  `;

  // ------------------------------------------------------------------ Kaart: Energieplan (tijdlijn)

  class ZonneburenEnergieplanCard extends ZbKaart {
    static get schema() {
      return [
        { name: "entity", label: "Sensor Energieplan", selector: { entity: { integration: "zonneburen", domain: "sensor" } } },
        { name: "titel", label: "Titel", selector: { text: {} } },
        { name: "uren", label: "Uren vooruit", selector: { number: { min: 6, max: 48, step: 6, mode: "slider" } } },
        { name: "apparaten", label: "Apparaten tonen", selector: { boolean: {} } },
      ];
    }
    getCardSize() { return 6; }

    _inhoud(st, hass) {
      const a = st.attributes || {};
      const nu = Date.now() / 1000;
      const uren = Number(this._config.uren) || 36;
      const rijen = Array.isArray(a.schema) && a.schema.length ? a.schema.map((r) => ({ t: r[0], stand: r[1], prijs: r[2], soc: r[9], bat: r[6] }))
        : (Array.isArray(a.kwartieren) ? a.kwartieren.map((k) => ({ t: k[0], stand: k[1], soc: k[2], prijs: k[3] })) : []);
      const zicht = rijen.filter((r) => r.t < nu + uren * 3600);
      const besparingId = broer(hass, this._id, "verwachte_besparing", "sensor");
      const besparing = besparingId ? Number(hass.states[besparingId].state) : NaN;
      const pauze = a.pauzeTot && a.pauzeTot * 1000 > Date.now();
      const status = pauze ? ["let-op", "Gepauzeerd tot " + tijd(a.pauzeTot)] : a.gestart ? ["goed", "Stuurt je batterij"]
        : a.advies ? ["", "Alleen advies"] : a.actief ? ["goed", "Volgt het plan"] : ["", "Alleen advies"];

      let tijdlijn = "";
      if (zicht.length > 1) {
        const B = 640, H = 170, L = 34, R = 8, PT = 6, PH = 16, ST = H - 16, SH = 10;
        const t0 = zicht[0].t, t1 = zicht[zicht.length - 1].t + KWARTIER;
        const x = (t) => L + (t - t0) / (t1 - t0) * (B - L - R);
        const yS = (soc) => 30 + (1 - soc / 100) * (ST - 38);
        const prijzen = zicht.map((r) => r.prijs).filter((p) => typeof p === "number").sort((p, q) => p - q);
        const laag = prijzen[Math.floor(prijzen.length / 10)] ?? 0, hoog = prijzen[Math.floor(prijzen.length * 9 / 10)] ?? 1;
        const prijs = zicht.map((r) => {
          if (typeof r.prijs !== "number") return "";
          const f = hoog - laag > 0.0001 ? klem((r.prijs - laag) / (hoog - laag), 0, 1) : 0.5;
          return `<rect x="${x(r.t).toFixed(1)}" y="${PT}" width="${(x(r.t + KWARTIER) - x(r.t) + 0.4).toFixed(1)}" height="${PH}" rx="1"
            fill="hsl(${(130 - f * 130).toFixed(0)} 70% 45%)" opacity=".75"><title>${tijd(r.t)} · ${euro3(r.prijs)} per kWh</title></rect>`;
        }).join("");
        const lijn = zicht.map((r, i) => `${i ? "L" : "M"}${x(r.t + KWARTIER).toFixed(1)} ${yS(r.soc).toFixed(1)}`).join(" ");
        const vlak = `${lijn} L${x(t1).toFixed(1)} ${yS(0).toFixed(1)} L${x(zicht[0].t + KWARTIER).toFixed(1)} ${yS(0).toFixed(1)} Z`;
        const standen = zicht.map((r) => `<rect x="${x(r.t).toFixed(1)}" y="${ST}" width="${(x(r.t + KWARTIER) - x(r.t) + 0.4).toFixed(1)}" height="${SH}"
          fill="${PLAN_KLEUR[r.stand] || KLEUR.grijs}"><title>${tijd(r.t)}: ${esc(PLAN_ZIN[r.stand] || r.stand)}</title></rect>`).join("");
        const raster = [0, 50, 100].map((s) => `<line x1="${L}" x2="${B - R}" y1="${yS(s).toFixed(1)}" y2="${yS(s).toFixed(1)}" class="raster"/><text x="${L - 6}" y="${(yS(s) + 3.5).toFixed(1)}" text-anchor="end">${s}%</text>`).join("");
        const stap = uren <= 12 ? 7200 : uren <= 24 ? 10800 : 21600;
        const tikken = [];
        for (let t = Math.ceil(t0 / stap) * stap; t < t1; t += stap) {
          const d = new Date(t * 1000);
          const label = d.getHours() === 0 ? d.toLocaleDateString("nl-NL", { weekday: "short" }) : tijd(t);
          tikken.push(`<line x1="${x(t).toFixed(1)}" x2="${x(t).toFixed(1)}" y1="${PT + PH + 2}" y2="${ST}" class="${d.getHours() === 0 ? "middernacht" : "raster"}"/><text x="${x(t).toFixed(1)}" y="${H + 12}" text-anchor="middle">${label}</text>`);
        }
        const nuX = nu > t0 && nu < t1 ? `<line x1="${x(nu).toFixed(1)}" x2="${x(nu).toFixed(1)}" y1="${PT}" y2="${ST + SH}" class="nu"/><text x="${(x(nu) + 4).toFixed(1)}" y="${PT + PH + 12}" text-anchor="start" class="nu-t">nu</text>` : "";
        tijdlijn = `<svg viewBox="0 0 ${B} ${H + 16}" class="tijdlijn" role="img" aria-label="Tijdlijn van het energieplan">
          ${prijs}${raster}${tikken.join("")}
          <path d="${vlak}" class="soc-vlak"/><path d="${lijn}" class="soc"/>
          ${standen}${nuX}
        </svg>`;
      }

      // Apparaten: per apparaat een baan op dezelfde tijdas.
      const app = (Array.isArray(a.apparaatblokken) ? a.apparaatblokken : []).filter((b) => b.van < nu + uren * 3600);
      let banen = "";
      if (this._config.apparaten !== false && app.length && zicht.length > 1) {
        const t0 = zicht[0].t, t1 = zicht[zicht.length - 1].t + KWARTIER;
        const pct = (t) => klem((t - t0) / (t1 - t0) * 100, 0, 100);
        const namen = [...new Set(app.map((b) => b.naam))];
        banen = `<h3 class="zb-h">Apparaten</h3><div class="banen">${namen.map((n) => `<div class="baan"><span title="${esc(n)}">${esc(n)}</span><div class="spoor">${app.filter((b) => b.naam === n).map((b) => {
          const kleur = b.stand === "blokkeren" ? KLEUR.grijs : b.stand === "laden" ? KLEUR.lucht : KLEUR.zon;
          return `<i style="left:${pct(b.van).toFixed(2)}%;width:${Math.max(0.8, pct(b.tot) - pct(b.van)).toFixed(2)}%;background:${kleur}" title="${esc(b.naam)} · ${dagTijd(b.van)}–${tijd(b.tot)} · ${esc(b.zin || "")}"></i>`;
        }).join("")}</div></div>`).join("")}</div>`;
      }

      const blokken = (Array.isArray(a.blokken) ? a.blokken : []).slice(0, 5);
      const lijst = blokken.map((b) => `<li style="--k:${PLAN_KLEUR[b.stand] || KLEUR.grijs}"><ha-icon icon="${PLAN_ICOON[b.stand] || "mdi:battery"}"></ha-icon><b>${b.van <= nu ? "nu" : dagTijd(b.van)} – ${tijd(b.tot)}</b><span>${esc(b.zin || PLAN_ZIN[b.stand] || b.stand)}</span></li>`).join("");

      return `<ha-card>
        <div class="zb-kop">
          <div>
            <div class="zb-titel">${esc(this._config.titel || "Energieplan")}</div>
            <div class="zb-sub"><span class="zb-chip ${status[0]}">${esc(status[1])}</span></div>
          </div>
          ${isFinite(besparing) ? `<div class="bedrag">${euro(Math.max(0, besparing))}<small>verwachte besparing</small></div>` : ""}
        </div>
        <div class="nu-zin" style="--k:${PLAN_KLEUR[(rijen.find((r) => r.t <= nu && nu < r.t + KWARTIER) || {}).stand] || KLEUR.grijs}"><i></i>Nu: ${esc(st.state)}${a.afwijking ? ` <small>(${esc(a.afwijking)})</small>` : ""}</div>
        ${a.geweigerd ? `<div class="zb-melding">Plan geweigerd: ${esc(a.geweigerd)}</div>` : ""}
        ${a.fout ? `<div class="zb-melding">${esc(a.fout)}</div>` : ""}
        ${tijdlijn}
        <div class="legenda">
          <span><i style="background:${KLEUR.lucht}"></i>Bijladen</span><span><i style="background:${KLEUR.groen}"></i>Uit de batterij</span>
          <span><i style="background:${KLEUR.violet}"></i>Bewaren</span><span><i style="background:${KLEUR.zon}"></i>Alleen zon</span>
          <span><i class="lijn"></i>Lading</span><span><i style="background:linear-gradient(90deg,hsl(130 70% 45%),hsl(0 70% 45%))"></i>Prijs laag → hoog</span>
        </div>
        ${lijst ? `<h3 class="zb-h">De komende blokken</h3><ul class="blokken">${lijst}</ul>` : ""}
        ${banen}
        ${a.geldig_tot ? `<div class="zb-voet">${a.advies ? "Dit is een advies: zet in de webapp de stand op Sturen om het uit te voeren. " : "Dit plan staat in Home Assistant en loopt door, ook zonder internet. "}Geldig tot ${new Date(a.geldig_tot * 1000).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit" })}.</div>` : ""}
      </ha-card>`;
    }

    _stijl() {
      return `
        .bedrag { font-size: 1.6rem; font-weight: 800; text-align: right; color: var(--primary-text-color); font-variant-numeric: tabular-nums; line-height: 1.1; }
        .bedrag small { display: block; font-size: .72rem; font-weight: 500; color: var(--secondary-text-color); }
        .nu-zin { display: flex; align-items: center; gap: 8px; margin: 4px 0 8px; font-weight: 600; color: var(--primary-text-color); }
        .nu-zin i { width: 10px; height: 10px; border-radius: 50%; background: var(--k); box-shadow: 0 0 0 4px color-mix(in srgb, var(--k) 25%, transparent); }
        .nu-zin small { font-weight: 400; color: var(--secondary-text-color); }
        .tijdlijn { width: 100%; height: auto; display: block; }
        .tijdlijn text { font-size: 10.5px; fill: var(--secondary-text-color); }
        .tijdlijn .raster { stroke: var(--zb-lijn); stroke-dasharray: 2 4; }
        .tijdlijn .middernacht { stroke: var(--zb-lijn); }
        .tijdlijn .soc { fill: none; stroke: var(--zb-groen); stroke-width: 2.2; stroke-linejoin: round; }
        .tijdlijn .soc-vlak { fill: var(--zb-groen); opacity: .12; }
        .tijdlijn .nu { stroke: var(--primary-text-color); stroke-width: 1.5; stroke-dasharray: 3 3; opacity: .7; }
        .tijdlijn .nu-t { font-weight: 700; fill: var(--primary-text-color); }
        .legenda { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: .75rem; color: var(--secondary-text-color); margin-top: 4px; }
        .legenda i { display: inline-block; width: 12px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
        .legenda i.lijn { height: 3px; background: var(--zb-groen); vertical-align: 2px; }
        .blokken { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
        .blokken li { display: grid; grid-template-columns: auto 8.5rem 1fr; align-items: center; gap: 10px; padding: 8px 12px; border-radius: 10px;
                      background: color-mix(in srgb, var(--k) 10%, var(--zb-zacht)); border-left: 4px solid var(--k); font-size: .88rem; }
        .blokken ha-icon { --mdc-icon-size: 18px; color: var(--k); }
        .blokken b { font-variant-numeric: tabular-nums; }
        .banen { display: grid; gap: 5px; }
        .baan { display: grid; grid-template-columns: 7rem 1fr; align-items: center; gap: 8px; font-size: .8rem; color: var(--secondary-text-color); }
        .baan span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .spoor { position: relative; height: 14px; border-radius: 7px; background: var(--zb-zacht); }
        .spoor i { position: absolute; top: 0; bottom: 0; border-radius: 7px; }
        @container (max-width: 420px) { .blokken li { grid-template-columns: auto 1fr; } .blokken li span { grid-column: 2; } .baan { grid-template-columns: 5rem 1fr; } }
      `;
    }
  }

  // ------------------------------------------------------------------ Kaart: Per kwartier (schema)

  class ZonneburenKwartierCard extends ZbKaart {
    static get schema() {
      return [
        { name: "entity", label: "Sensor Energieplan", selector: { entity: { integration: "zonneburen", domain: "sensor" } } },
        { name: "titel", label: "Titel", selector: { text: {} } },
        { name: "filter", label: "Standaard tonen", selector: { select: { mode: "dropdown", options: [
          { value: "ingrepen", label: "Alleen ingrepen" }, { value: "24", label: "Komende 24 uur" }, { value: "alles", label: "Het hele plan" }] } } },
        { name: "max_hoogte", label: "Maximale hoogte (px, 0 = alles)", selector: { number: { min: 0, max: 2000, step: 50, mode: "box" } } },
      ];
    }
    getCardSize() { return 8; }
    _extraSleutel() { return this._filter || ""; }

    _klik(ev) {
      const knop = ev.target.closest("[data-filter]");
      if (knop) { this._filter = knop.dataset.filter; this._vorige = null; this.hass = this._hass; }
    }

    _inhoud(st) {
      const a = st.attributes || {};
      const nu = Date.now() / 1000;
      const filter = this._filter || this._config.filter || "ingrepen";
      const lasten = Array.isArray(a.schema_lasten) ? a.schema_lasten : (Array.isArray(a.apparaatblokken) ? a.apparaatblokken : []);
      if (!Array.isArray(a.schema) || !a.schema.length) {
        return `<ha-card><div class="zb-kop"><div class="zb-titel">${esc(this._config.titel || "Per kwartier")}</div></div>
          <div class="zb-melding info">Het schema per kwartier komt mee met de nieuwste versie van de Zonneburen-server. Zodra er een nieuw plan is (elk kwartier), verschijnt het hier.</div></ha-card>`;
      }
      const alle = a.schema.map((r) => ({ t: r[0], stand: r[1], in: r[2], uit: r[3], zon: r[4], verbruik: r[5], bat: r[6], net: r[7], doel: r[8], soc: r[9], voorspeld: r[10] }))
        .filter((r) => r.t + KWARTIER > nu);
      const lastIn = (r) => lasten.filter((l) => l.van < r.t + KWARTIER && l.tot > r.t);
      const actief = (r) => r.stand !== "zelfverbruik" || Math.abs(r.bat || 0) > 100 || lastIn(r).length > 0;
      const rijen = filter === "alles" ? alle : filter === "24" ? alle.filter((r) => r.t < nu + 86400) : alle.filter(actief);
      const prijzen = rijen.map((r) => r.in).filter((p) => typeof p === "number");
      const pLaag = Math.min(...prijzen), pHoog = Math.max(...prijzen);
      const batMax = Math.max(500, ...rijen.map((r) => Math.abs(r.bat || 0)));
      const aantal = rijen.length;
      const sub = filter === "alles" ? `${aantal} kwartieren, tot het einde van het plan` : filter === "24" ? `${aantal} kwartieren, de komende 24 uur`
        : aantal ? `${aantal} ${aantal === 1 ? "kwartier" : "kwartieren"} waarin de batterij of een apparaat iets doet` : "geen ingrepen gepland";
      const W = (w) => (typeof w !== "number" ? "–" : Math.abs(w) >= 1000 ? (Math.abs(w) / 1000).toFixed(1).replace(".", ",") + " kW" : Math.round(Math.abs(w)) + " W");

      let body = "", dag = null;
      for (const r of rijen) {
        const d = dagKop(r.t);
        if (d !== dag) { dag = d; body += `<tr class="dag"><th colspan="9">${esc(d)}</th></tr>`; }
        const bat = (r.bat || 0) < -50 ? "laad" : (r.bat || 0) > 50 ? "ontlaad" : "";
        const pp = pHoog - pLaag > 0.005 ? (r.in - pLaag) / (pHoog - pLaag) : 0.5;
        const nuRij = r.t <= nu && nu < r.t + KWARTIER;
        const balk = Math.min(1, Math.abs(r.bat || 0) / batMax) * 50;
        body += `<tr class="${nuRij ? "nu" : ""}">
          <td class="tijd">${tijd(r.t)}${nuRij ? "<small>nu</small>" : ""}</td>
          <td class="stand"><span class="chip" style="--k:${PLAN_KLEUR[r.stand] || KLEUR.grijs}">${esc(PLAN_ZIN[r.stand] || r.stand)}</span>${lastIn(r).map((l) => `<span class="chip app"><ha-icon icon="mdi:power-plug"></ha-icon>${esc(l.naam)}${l.stand === "blokkeren" ? " pauze" : ""}</span>`).join("")}</td>
          <td class="r prijs" data-k="Prijs"><span style="background:hsl(${(130 - klem(pp, 0, 1) * 130).toFixed(0)} 65% 45% / .2)">${euro3(r.in)}${r.voorspeld ? "*" : ""}</span></td>
          <td class="r" data-k="Zon">${(r.zon || 0) >= 10 ? W(r.zon) : "–"}</td>
          <td class="r" data-k="Verbruik">${W(r.verbruik)}</td>
          <td class="bat ${bat}" data-k="${bat === "laad" ? "Laadt" : bat === "ontlaad" ? "Ontlaadt" : "Batterij"}"><span class="balk"><i style="${(r.bat || 0) < 0 ? "right" : "left"}:50%;width:${balk.toFixed(1)}%"></i></span><span>${bat ? `<em>${bat === "laad" ? "laadt " : "ontlaadt "}</em>${W(r.bat)}` : "rust"}</span></td>
          <td class="r net ${(r.net || 0) < -50 ? "terug" : ""}" data-k="${(r.net || 0) < -50 ? "Terug" : "Net"}">${Math.abs(r.net || 0) < 50 ? "0" : ((r.net < 0 ? "↑ " : "↓ ") + W(r.net))}</td>
          <td class="r doel" data-k="Netdoel">${typeof r.doel === "number" ? W(r.doel) : ""}</td>
          <td class="soc" data-k="Lading"><span class="balk"><i style="width:${klem(r.soc || 0, 0, 100)}%"></i></span><span>${Math.round(r.soc || 0)}%</span></td>
        </tr>`;
      }
      const hoogte = Number(this._config.max_hoogte ?? 520);
      return `<ha-card>
        <div class="zb-kop">
          <div><div class="zb-titel">${esc(this._config.titel || "Per kwartier")}</div><div class="zb-sub">${esc(sub)}${a.advies ? ` · <span class="zb-chip">advies</span>` : ""}</div></div>
          <div class="zb-segment" role="group" aria-label="Welke kwartieren">
            ${[["ingrepen", "Ingrepen"], ["24", "24 uur"], ["alles", "Hele plan"]].map(([k, l]) => `<button data-filter="${k}" class="${filter === k ? "aan" : ""}">${l}</button>`).join("")}
          </div>
        </div>
        ${aantal ? `<div class="wrap" style="${hoogte > 0 ? `max-height:${hoogte}px` : ""}"><table>
          <thead><tr><th>Tijd</th><th>Wat er gebeurt</th><th class="r">Prijs</th><th class="r">Zon</th><th class="r">Verbruik</th><th>Batterij</th><th class="r">Net</th><th class="r doel">Netdoel</th><th>Lading</th></tr></thead>
          <tbody>${body}</tbody></table></div>`
        : `<div class="zb-melding info">Geen ingrepen gepland: je batterij gebruikt gewoon je eigen stroom. Kies <b>24 uur</b> om alle kwartieren te zien.</div>`}
        <div class="zb-voet">Prijs per kWh, groen is goedkoop en rood duur binnen deze lijst. * = verwachte prijs. Net: ↓ van het net, ↑ naar het net. Lading aan het eind van het kwartier.</div>
      </ha-card>`;
    }

    _stijl() {
      return `
        .wrap { overflow: auto; border: 1px solid var(--zb-lijn); border-radius: 12px; overscroll-behavior: contain; }
        table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .82rem; font-variant-numeric: tabular-nums; }
        thead th { position: sticky; top: 0; z-index: 2; background: var(--card-background-color, #fff); color: var(--secondary-text-color);
                   font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; text-align: left; padding: 9px 10px;
                   border-bottom: 1px solid var(--zb-lijn); white-space: nowrap; }
        td { padding: 7px 10px; border-bottom: 1px solid var(--zb-lijn); color: var(--primary-text-color); white-space: nowrap; vertical-align: middle; }
        .r { text-align: right; }
        tr.dag th { padding: 6px 10px; background: var(--zb-zacht); color: var(--primary-text-color); font-size: .78rem; font-weight: 700; text-align: left; }
        tr.nu td { background: rgba(2,132,199,.09); }
        tr.nu td.tijd { box-shadow: inset 3px 0 0 var(--zb-lucht); }
        td.tijd { font-weight: 700; }
        td.tijd small { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 99px; background: var(--zb-lucht); color: #fff; font-size: .66rem; vertical-align: 1px; }
        td.stand { white-space: normal; min-width: 190px; }
        .chip { display: inline-flex; align-items: center; gap: 5px; margin: 1px 4px 1px 0; padding: 2px 9px 2px 7px; border-radius: 99px; white-space: nowrap;
                background: color-mix(in srgb, var(--k, #94a3b8) 16%, transparent); font-size: .76rem; font-weight: 600; }
        .chip::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--k, #94a3b8); }
        .chip.app { background: none; border: 1px solid var(--zb-lijn); }
        .chip.app::before { display: none; }
        .chip ha-icon { --mdc-icon-size: 13px; }
        td.prijs span { display: inline-block; padding: 2px 7px; border-radius: 7px; font-weight: 600; }
        td.net.terug { color: var(--zb-groen); }
        td.bat, td.soc { min-width: 128px; }
        td.bat.laad { color: var(--zb-lucht); } td.bat.ontlaad { color: var(--zb-groen); }
        td.bat:not(.laad):not(.ontlaad) { color: var(--secondary-text-color); }
        td.bat em { font-style: normal; }
        .balk { position: relative; display: inline-block; width: 52px; height: 8px; margin-right: 8px; border-radius: 4px; background: var(--zb-zacht); vertical-align: middle; overflow: hidden; }
        .balk i { position: absolute; top: 0; bottom: 0; left: 0; border-radius: 4px; background: var(--zb-violet); }
        td.bat .balk::after { content: ""; position: absolute; left: 50%; top: -1px; bottom: -1px; width: 1px; background: var(--zb-lijn); }
        td.bat .balk i { left: auto; border-radius: 0; background: currentColor; }
        @container (max-width: 640px) {
          thead { display: none; }
          table, tbody { display: block; }
          tr.dag { display: block; } tr.dag th { display: block; }
          tbody tr:not(.dag) { display: grid; grid-template-columns: 2.7rem repeat(6, minmax(0, 1fr)); gap: 6px 8px; padding: 10px; border-bottom: 1px solid var(--zb-lijn); }
          tbody tr.nu { background: rgba(2,132,199,.09); box-shadow: inset 3px 0 0 var(--zb-lucht); }
          tbody tr.nu td { background: none; } tr.nu td.tijd { box-shadow: none; }
          td { display: block; padding: 0; border: 0; min-width: 0 !important; white-space: normal; text-align: left !important; }
          td.doel { display: none; }
          td.tijd { grid-row: 1 / span 3; } td.tijd small { display: block; width: fit-content; margin: 4px 0 0; }
          td.stand { grid-column: 2 / span 4; grid-row: 1; } .chip { white-space: normal; }
          td.prijs { grid-column: 6 / span 2; grid-row: 1; text-align: right !important; white-space: nowrap; }
          td[data-k]:not(.prijs) { grid-row: 2; grid-column: span 2; font-size: .8rem; font-weight: 600; white-space: nowrap; }
          td[data-k]:not(.prijs)::before { content: attr(data-k); display: block; color: var(--secondary-text-color); font-size: .66rem; font-weight: 500; }
          td.bat[data-k], td.soc[data-k] { grid-row: 3; grid-column: span 3; }
          td.bat[data-k] { grid-column: 2 / span 3; }
          td.bat em { display: none; }
          .balk { display: block; width: 100%; height: 4px; margin: 2px 0 3px; }
        }
      `;
    }
  }

  // ------------------------------------------------------------------ Kaart: Batterij nu (status en bediening)

  class ZonneburenBatterijCard extends ZbKaart {
    static get schema() {
      return [
        { name: "entity", label: "Sensor Energieplan", selector: { entity: { integration: "zonneburen", domain: "sensor" } } },
        { name: "titel", label: "Titel", selector: { text: {} } },
        { name: "bediening", label: "Knoppen tonen (pauzeren, uitvoeren)", selector: { boolean: {} } },
      ];
    }
    getCardSize() { return 4; }
    _extraSleutel(hass, id) {
      const sw = broer(hass, id, "energieplan_uitvoeren", "switch");
      return sw && hass.states[sw] ? hass.states[sw].state : "";
    }

    _klik(ev) {
      const knop = ev.target.closest("[data-actie]");
      if (!knop || !this._hass) return;
      const [domein, sleutel] = knop.dataset.actie.split(":");
      const id = broer(this._hass, this._id, sleutel, domein);
      if (!id) return;
      if (domein === "switch") this._hass.callService("switch", "toggle", { entity_id: id });
      else this._hass.callService("button", "press", { entity_id: id });
    }

    _inhoud(st, hass) {
      const a = st.attributes || {};
      const nu = Date.now() / 1000;
      const rij = (Array.isArray(a.schema) ? a.schema : []).find((r) => r[0] <= nu && nu < r[0] + KWARTIER);
      const stand = rij ? rij[1] : a.stand || "zelfverbruik";
      const kleur = PLAN_KLEUR[stand] || KLEUR.grijs;
      const soc = typeof a.soc_nu === "number" ? a.soc_nu : rij ? rij[9] : null;
      const batW = typeof a.batterij_w === "number" ? a.batterij_w : rij ? rij[6] : null;
      const netW = typeof a.net_w === "number" ? a.net_w : rij ? rij[7] : null;
      const prijs = rij ? rij[2] : null;
      const pauze = a.pauzeTot && a.pauzeTot * 1000 > Date.now();
      const sw = broer(hass, this._id, "energieplan_uitvoeren", "switch");
      const uitvoeren = sw && hass.states[sw] ? hass.states[sw].state === "on" : null;
      const status = pauze ? ["let-op", "mdi:pause-circle", "Gepauzeerd tot " + tijd(a.pauzeTot)]
        : a.gestart ? ["goed", "mdi:check-circle", "Zonneburen stuurt je batterij"]
        : a.advies ? ["", "mdi:eye", "Alleen advies: zet de stand op Sturen in de webapp"]
        : a.actief ? ["goed", "mdi:check-circle", "Klaar om te sturen"]
        : uitvoeren === false ? ["let-op", "mdi:power", "Energieplan uitvoeren staat uit"]
        : ["", "mdi:information", "Alleen advies"];
      const besparingId = broer(hass, this._id, "verwachte_besparing", "sensor");
      const besparing = besparingId ? Number(hass.states[besparingId].state) : NaN;
      const volgende = (Array.isArray(a.blokken) ? a.blokken : []).filter((b) => b.van > nu).slice(0, 2);
      const W = (w) => (Math.abs(w) >= 1000 ? (Math.abs(w) / 1000).toFixed(1).replace(".", ",") + " kW" : Math.round(Math.abs(w)) + " W");
      const batTekst = batW === null ? "–" : Math.abs(batW) < 50 ? "rust" : batW < 0 ? "laadt " + W(batW) : "levert " + W(batW);
      const netTekst = netW === null ? "–" : Math.abs(netW) < 50 ? "in balans" : netW < 0 ? "↑ " + W(netW) + " terug" : "↓ " + W(netW) + " afname";
      const knoppen = this._config.bediening === false ? "" : `<div class="knoppen">
          ${sw ? `<button data-actie="switch:energieplan_uitvoeren" class="${uitvoeren ? "aan" : ""}"><ha-icon icon="mdi:${uitvoeren ? "toggle-switch" : "toggle-switch-off-outline"}"></ha-icon>Energieplan uitvoeren</button>` : ""}
          ${pauze ? `<button data-actie="button:energieplan_hervat"><ha-icon icon="mdi:play"></ha-icon>Hervatten</button>`
                  : `<button data-actie="button:energieplan_pauze"><ha-icon icon="mdi:pause"></ha-icon>2 uur pauzeren</button>`}
        </div>`;
      return `<ha-card style="--k:${kleur}">
        <div class="held">
          <div class="ring" style="--soc:${soc === null ? 0 : klem(soc, 0, 100)}">
            <div><b>${soc === null ? "–" : procent(soc)}</b><small>lading</small></div>
          </div>
          <div class="tekst">
            <div class="boven">${esc(this._config.titel || "Je batterij")}</div>
            <div class="stand"><ha-icon icon="${PLAN_ICOON[stand] || "mdi:battery"}"></ha-icon>${esc(PLAN_ZIN[stand] || st.state)}</div>
            <span class="zb-chip ${status[0]}"><ha-icon icon="${status[1]}"></ha-icon>${esc(status[2])}</span>
            ${a.afwijking ? `<div class="afwijking">${esc(a.afwijking)}</div>` : ""}
          </div>
        </div>
        <div class="cijfers">
          <div><small>Batterij nu</small><b>${batTekst}</b></div>
          <div><small>Net nu</small><b>${netTekst}</b></div>
          <div><small>Prijs nu</small><b>${prijs === null ? "–" : euro3(prijs)}</b></div>
          <div><small>Verwachte besparing</small><b>${isFinite(besparing) ? euro(Math.max(0, besparing)) : "–"}</b></div>
        </div>
        ${volgende.length ? `<div class="straks">${volgende.map((b) => `<span style="--k:${PLAN_KLEUR[b.stand] || KLEUR.grijs}"><i></i>${dagTijd(b.van)} · ${esc(b.zin || PLAN_ZIN[b.stand] || b.stand)}</span>`).join("")}</div>` : ""}
        ${a.geweigerd ? `<div class="zb-melding">Plan geweigerd: ${esc(a.geweigerd)}</div>` : ""}
        ${a.fout ? `<div class="zb-melding">${esc(a.fout)}</div>` : ""}
        ${knoppen}
      </ha-card>`;
    }

    _stijl() {
      return `
        ha-card { background: linear-gradient(135deg, color-mix(in srgb, var(--k) 12%, var(--ha-card-background, var(--card-background-color, #fff))), var(--ha-card-background, var(--card-background-color, #fff)) 60%); }
        .held { display: flex; gap: 18px; align-items: center; }
        .ring { --soc: 0; flex: none; width: 104px; aspect-ratio: 1; border-radius: 50%; display: grid; place-items: center;
                background: conic-gradient(var(--zb-groen) calc(var(--soc) * 1%), var(--zb-zacht) 0); }
        .ring > div { width: 78%; aspect-ratio: 1; border-radius: 50%; background: var(--ha-card-background, var(--card-background-color, #fff)); display: grid; place-content: center; text-align: center; }
        .ring b { font-size: 1.6rem; font-weight: 800; color: var(--primary-text-color); line-height: 1; }
        .ring small { font-size: .7rem; color: var(--secondary-text-color); }
        .tekst { min-width: 0; display: grid; gap: 6px; justify-items: start; }
        .boven { font-size: .8rem; font-weight: 600; color: var(--secondary-text-color); }
        .stand { display: flex; align-items: center; gap: 8px; font-size: 1.25rem; font-weight: 700; color: var(--primary-text-color); line-height: 1.25; }
        .stand ha-icon { --mdc-icon-size: 26px; color: var(--k); }
        .zb-chip ha-icon { --mdc-icon-size: 15px; }
        .afwijking { font-size: .8rem; color: var(--secondary-text-color); }
        .cijfers { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 16px; }
        .cijfers div { padding: 10px 12px; border-radius: 12px; background: var(--zb-zacht); min-width: 0; }
        .cijfers small { display: block; font-size: .7rem; font-weight: 600; color: var(--secondary-text-color); }
        .cijfers b { font-size: .98rem; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
        .straks { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 12px; font-size: .82rem; color: var(--secondary-text-color); }
        .straks i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--k); margin-right: 6px; }
        .knoppen { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
        .knoppen button { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border-radius: 99px; border: 1px solid var(--zb-lijn);
                          background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color); font: inherit; font-size: .85rem; font-weight: 600; cursor: pointer; }
        .knoppen button.aan { border-color: var(--zb-groen); color: var(--zb-groen); }
        .knoppen ha-icon { --mdc-icon-size: 18px; }
        @container (max-width: 460px) { .cijfers { grid-template-columns: repeat(2, minmax(0, 1fr)); } .ring { width: 86px; } }
      `;
    }
  }

  // ------------------------------------------------------------------ Kaart: Apparaten in het plan

  class ZonneburenApparatenCard extends ZbKaart {
    static get schema() {
      return [
        { name: "entity", label: "Sensor Energieplan", selector: { entity: { integration: "zonneburen", domain: "sensor" } } },
        { name: "titel", label: "Titel", selector: { text: {} } },
      ];
    }
    _extraSleutel(hass) {
      return Object.keys(hass.states).filter((id) => "volgt_plan" in (hass.states[id].attributes || {})).map((id) => hass.states[id].last_updated).join();
    }
    _klik(ev) {
      const regel = ev.target.closest("[data-id]");
      if (regel) this._toonMeer(regel.dataset.id);
    }

    _inhoud(st, hass) {
      const nu = Date.now() / 1000;
      const ids = Object.keys(hass.states).filter((id) => id.startsWith("sensor.") && "volgt_plan" in (hass.states[id].attributes || {})).sort();
      const t0 = nu, t1 = nu + 86400;
      const pct = (t) => klem((t - t0) / (t1 - t0) * 100, 0, 100);
      // Op hele uren: elke zes uur vanaf het eerstvolgende zesvoud.
      const eerste = new Date(); eerste.setMinutes(0, 0, 0); eerste.setHours(Math.ceil((eerste.getHours() + 1) / 6) * 6);
      const tikken = [0, 6, 12, 18].map((u) => eerste.getTime() / 1000 + u * 3600).filter((t) => t < t1)
        .map((t) => `<span style="left:${pct(t)}%">${tijd(t)}</span>`).join("");
      const regels = ids.map((id) => {
        const s = hass.states[id];
        const naam = (s.attributes.friendly_name || id).replace(/^.*Energieplan /, "");
        const blokken = (s.attributes.blokken || []).filter((b) => b.van < t1);
        const aan = s.attributes.aan_volgens_plan === true;
        return `<li data-id="${esc(id)}" class="${aan ? "aan" : ""}">
          <div class="naam"><ha-icon icon="${aan ? "mdi:power-plug" : "mdi:power-plug-off-outline"}"></ha-icon><div><b>${esc(naam)}</b><small>${esc(s.state)}</small></div></div>
          <div class="spoor">${blokken.map((b) => {
            const kleur = b.stand === "blokkeren" ? KLEUR.grijs : b.stand === "laden" ? KLEUR.lucht : KLEUR.zon;
            return `<i style="left:${pct(b.van).toFixed(2)}%;width:${Math.max(1, pct(b.tot) - pct(b.van)).toFixed(2)}%;background:${kleur}" title="${dagTijd(b.van)}–${tijd(b.tot)} · ${esc(b.zin || "")}"></i>`;
          }).join("")}</div>
        </li>`;
      }).join("");
      return `<ha-card>
        <div class="zb-kop"><div><div class="zb-titel">${esc(this._config.titel || "Apparaten in het plan")}</div><div class="zb-sub">de komende 24 uur</div></div></div>
        ${ids.length ? `<div class="as"><span></span><div>${tikken}</div></div><ul>${regels}</ul>`
          : `<div class="zb-melding info">Er staan nog geen apparaten in het energieplan. Zet ze aan bij Energieplan in de webapp, onder Apparaten.</div>`}
      </ha-card>`;
    }

    _stijl() {
      return `
        ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
        li { display: grid; grid-template-columns: minmax(0, 11rem) 1fr; gap: 12px; align-items: center; padding: 8px 10px; border-radius: 12px; background: var(--zb-zacht); cursor: pointer; }
        li.aan { box-shadow: inset 3px 0 0 var(--zb-zon); }
        .naam { display: flex; gap: 10px; align-items: center; min-width: 0; }
        .naam ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); flex: none; }
        li.aan .naam ha-icon { color: var(--zb-zon); }
        .naam div { min-width: 0; }
        .naam b { display: block; font-size: .9rem; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .naam small { display: block; font-size: .75rem; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .spoor { position: relative; height: 16px; border-radius: 8px; background: var(--ha-card-background, var(--card-background-color, #fff)); }
        .spoor i { position: absolute; top: 0; bottom: 0; border-radius: 8px; }
        .as { display: grid; grid-template-columns: minmax(0, 11rem) 1fr; gap: 12px; padding: 0 10px 4px; }
        .as > div { position: relative; height: 14px; font-size: .68rem; color: var(--secondary-text-color); }
        .as span { position: absolute; transform: translateX(-50%); }
        @container (max-width: 420px) { li, .as { grid-template-columns: minmax(0, 7.5rem) 1fr; } }
      `;
    }
  }

  // ------------------------------------------------------------------ Kaart: Virtuele buurtbatterij

  class ZonneburenBuurtbatterijCard extends ZbKaart {
    static get soort() { return "buurtbatterij"; }
    static get schema() {
      return [
        { name: "entity", label: "Sensor Buurtbatterij", selector: { entity: { integration: "zonneburen", domain: "sensor" } } },
        { name: "titel", label: "Titel", selector: { text: {} } },
      ];
    }

    _inhoud(st) {
      const a = st.attributes || {};
      if (!a.aantal) {
        return `<ha-card><div class="zb-kop"><div class="zb-titel">${esc(this._config.titel || "Virtuele buurtbatterij")}</div></div>
          <div class="zb-melding info">Nog geen thuisbatterijen in de buurtbatterij. Heb je een batterij? Zet in de webapp bij je energieplan <b>Meedoen met de wijk</b> aan.</div>
          ${this._gedeeld(a)}</ha-card>`;
      }
      const p = typeof a.procent === "number" ? a.procent : 0;
      const w = a.wijk_w || 0;
      const richting = w < -100 ? "laadt" : w > 100 ? "levert" : "";
      const W = (v) => (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(1).replace(".", ",") + " kW" : Math.round(Math.abs(v)) + " W");
      return `<ha-card>
        <div class="zb-kop">
          <div><div class="zb-titel">${esc(this._config.titel || "Virtuele buurtbatterij")}</div>
            <div class="zb-sub">${a.aantal} ${a.aantal === 1 ? "thuisbatterij" : "thuisbatterijen"} · ${a.sturen || 0} ${a.sturen === 1 ? "stuurt" : "sturen"} mee</div></div>
          <span class="zb-chip ${a.aan ? "goed" : ""}">${a.aan ? "Stuurt samen" : "Alleen inzicht"}</span>
        </div>
        <div class="held">
          <div class="accu ${richting} ${p < 15 ? "laag" : ""} ${p < 60 ? "naast" : ""}" style="--vul:${klem(p, 0, 100) / 100}">
            <div class="romp"><div class="vul"></div><div class="streep"></div>
              ${richting ? `<div class="pijlen">${[0, 1, 2].map(() => `<ha-icon icon="mdi:chevron-${richting === "laadt" ? "right" : "left"}"></ha-icon>`).join("")}</div>` : ""}
              <b>${procent(p)}</b></div>
            <div class="kap"></div>
          </div>
          <div class="getal"><b>${kwh(a.opgeslagen_kwh || 0)}</b><small>van ${kwh(a.bruikbaar_kwh || 0)}</small>
            <span class="zb-chip ${richting ? "goed" : ""}">${richting === "laadt" ? "laadt " + W(w) + " uit de wijk" : richting === "levert" ? "levert " + W(w) + " aan de wijk" : "geen uitwisseling met de wijk"}</span></div>
        </div>
        ${this._gedeeld(a)}
        ${typeof a.week_gedeeld === "number" ? `<div class="zb-voet">Deze week gebruikten buren ${procent(a.week_gedeeld * 100)} van alle teruglevering op hetzelfde moment zelf. Gelijktijdigheid levert iedereen het meeste op.</div>` : ""}
      </ha-card>`;
    }

    _gedeeld(a) {
      if (typeof a.gedeeld_nu_watt !== "number") return "";
      const W = (v) => (Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(1).replace(".", ",") + " kW" : Math.round(Math.abs(v)) + " W");
      return `<div class="gedeeld"><span>Nu tegelijk gedeeld tussen buren</span><b>${W(a.gedeeld_nu_watt)}</b></div>`;
    }

    _stijl() {
      return `
        .held { display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }
        .getal b { display: block; font-size: 1.7rem; font-weight: 800; color: var(--primary-text-color); line-height: 1.1; }
        .getal small { display: block; color: var(--secondary-text-color); margin-bottom: 8px; }
        .accu { --vul: 0; display: flex; align-items: center; width: 190px; height: 100px; }
        .romp { position: relative; flex: 1; align-self: stretch; border: 3px solid var(--zb-lijn); border-radius: 18px; background: var(--zb-zacht); overflow: hidden; }
        .kap { width: 9px; height: 38%; margin-left: -1px; border-radius: 0 6px 6px 0; background: var(--zb-lijn); }
        .vul { position: absolute; left: 5px; top: 5px; bottom: 5px; width: calc((100% - 10px) * var(--vul)); min-width: 3px; border-radius: 12px;
               background: linear-gradient(90deg, #047857, #10b981 60%, #6ee7b7); transition: width 1.2s ease; }
        .accu.laag .vul { background: linear-gradient(90deg, #b45309, #f59e0b); }
        .streep { position: absolute; inset: 5px; background: repeating-linear-gradient(90deg, transparent 0 calc(25% - 1px), var(--zb-lijn) calc(25% - 1px) 25%); }
        .romp b { position: absolute; left: 0; right: 0; top: 50%; transform: translateY(-50%); text-align: center; font-size: 1.4rem; font-weight: 800; color: #fff; text-shadow: 0 1px 5px rgba(0,0,0,.35); }
        .accu.naast .romp b { right: auto; left: calc(5px + (100% - 10px) * var(--vul) + 8px); color: var(--primary-text-color); text-shadow: none; }
        .pijlen { position: absolute; top: 6px; right: 8px; display: flex; padding: 0 4px; border-radius: 8px; background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--zb-lucht); }
        .accu.laadt .pijlen { color: var(--zb-groen); }
        .pijlen ha-icon { --mdc-icon-size: 16px; margin: 0 -4px; animation: zb-pijl 1.4s linear infinite; opacity: .2; }
        .accu.laadt .pijlen ha-icon:nth-child(2), .accu.levert .pijlen ha-icon:nth-child(2) { animation-delay: .45s; }
        .accu.laadt .pijlen ha-icon:nth-child(3), .accu.levert .pijlen ha-icon:nth-child(1) { animation-delay: .9s; }
        @keyframes zb-pijl { 0% { opacity: .15; } 30% { opacity: .95; } 100% { opacity: .15; } }
        .gedeeld { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; margin-top: 14px; padding: 12px 14px; border-radius: 12px; background: rgba(5,150,105,.12); font-size: .85rem; color: var(--primary-text-color); }
        .gedeeld b { font-size: 1.15rem; color: var(--zb-groen); }
        @media (prefers-reduced-motion: reduce) { .pijlen ha-icon { animation: none; opacity: .8; } }
      `;
    }
  }

  // ------------------------------------------------------------------ De editor voor alle kaarten

  // Een formulier van Home Assistant zelf (ha-form) met het schema van de kaart: de sensor kies je uit een
  // lijst met alleen de entiteiten van Zonneburen.
  class ZonneburenKaartEditor extends HTMLElement {
    setConfig(config) { this._config = { ...(config || {}) }; this._teken(); }
    set hass(hass) { this._hass = hass; this._teken(); }
    _teken() {
      if (!this._hass || !this._config) return;
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.computeLabel = (s) => s.label || s.name;
        this._form.addEventListener("value-changed", (ev) => {
          this._config = { ...ev.detail.value };
          this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
        });
        this.appendChild(this._form);
      }
      this._form.hass = this._hass;
      this._form.schema = this.schema || [];
      this._form.data = this._config;
    }
  }

  // ------------------------------------------------------------------ Paneel in de zijbalk

  // Het paneel Energieplan dat de integratie registreert: zoekt zelf de sensoren van Zonneburen, dus de
  // bewoner hoeft niets in te stellen. Drie tabbladen: je batterij, het schema per kwartier, en de wijk.
  class ZonneburenEnergieplanPanel extends HTMLElement {
    set hass(hass) {
      this._hass = hass;
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.addEventListener("click", (ev) => {
          const tab = ev.target.closest("[data-tab]");
          if (tab) { this._tab = tab.dataset.tab; this._sleutel = null; this.hass = this._hass; }
        });
      }
      const plannen = Object.keys(hass.states).filter((id) => id.startsWith("sensor.") && SOORT.energieplan(hass.states[id])).sort();
      const wijk = zoek(hass, "wijk"), bb = zoek(hass, "buurtbatterij");
      const tab = this._tab || "plan";
      const sleutel = [plannen.join(), wijk, bb, tab].join("|");
      if (sleutel !== this._sleutel) { this._sleutel = sleutel; this._bouw(plannen, wijk, bb, tab); }
      this.shadowRoot.querySelectorAll(".kaarten > *").forEach((k) => { k.hass = hass; });
      const menu = this.shadowRoot.querySelector("ha-menu-button");
      if (menu) { menu.hass = hass; menu.narrow = this.narrow; }
    }

    set narrow(n) { this._narrow = n; }
    get narrow() { return !!this._narrow; }

    _bouw(plannen, wijk, bb, tab) {
      const kaarten = [];
      const titel = (id, standaard) => (plannen.length > 1 ? (this._hass.states[id].attributes.friendly_name || standaard).replace(/ Energieplan$/, "") + " · " : "") + standaard;
      if (tab === "plan") {
        for (const id of plannen) {
          kaarten.push(["zonneburen-batterij-card", { entity: id, titel: titel(id, "Je batterij") }, "breed"]);
          kaarten.push(["zonneburen-energieplan-card", { entity: id, titel: titel(id, "Energieplan"), apparaten: false }, "breed"]);
          kaarten.push(["zonneburen-apparaten-card", { entity: id, titel: titel(id, "Apparaten in het plan") }, "breed"]);
        }
      } else if (tab === "kwartier") {
        for (const id of plannen) kaarten.push(["zonneburen-kwartier-card", { entity: id, titel: titel(id, "Per kwartier"), max_hoogte: 0 }, "breed"]);
      } else {
        if (bb) kaarten.push(["zonneburen-buurtbatterij-card", { entity: bb }, "breed"]);
        if (wijk) kaarten.push(["zonneburen-card", { entity: wijk, titel: "De wijk" }, "breed"]);
      }
      const leeg = !kaarten.length
        ? `<div class="leeg"><b>Nog niets te zien.</b><br>${tab === "wijk" ? "De wijkgegevens komen binnen zodra Home Assistant met Zonneburen verbonden is." : "Zet in de webapp bij Energieplan de stand op Adviseren of Sturen. Het plan komt dan binnen enkele minuten binnen, en blijft 36 uur staan, ook zonder internet."}</div>`
        : "";
      this.shadowRoot.innerHTML = `
        <div class="balk"><ha-menu-button></ha-menu-button><span>Energieplan</span></div>
        <nav class="tabs">
          ${[["plan", "mdi:battery-clock", "Overzicht"], ["kwartier", "mdi:table-clock", "Per kwartier"], ["wijk", "mdi:home-group", "Wijk"]]
            .map(([k, i, l]) => `<button data-tab="${k}" class="${tab === k ? "aan" : ""}"><ha-icon icon="${i}"></ha-icon>${l}</button>`).join("")}
        </nav>
        <div class="inhoud">${leeg}<div class="kaarten"></div></div>
        <style>
          :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color); }
          .balk { display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px); padding: 0 12px; font-size: 20px;
                  background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); }
          .tabs { display: flex; gap: 4px; padding: 0 12px; background: var(--app-header-background-color, var(--primary-color)); overflow-x: auto; }
          .tabs button { display: inline-flex; align-items: center; gap: 6px; padding: 10px 14px; border: 0; border-bottom: 3px solid transparent; background: none;
                         color: var(--app-header-text-color, #fff); opacity: .75; font: inherit; font-weight: 600; cursor: pointer; white-space: nowrap; }
          .tabs button.aan { opacity: 1; border-bottom-color: var(--app-header-text-color, #fff); }
          .tabs ha-icon { --mdc-icon-size: 18px; }
          .inhoud { max-width: 1100px; margin: 0 auto; padding: 16px; }
          .kaarten { display: grid; gap: 16px; }
          .leeg { padding: 24px; border-radius: 12px; background: var(--card-background-color); line-height: 1.5; }
        </style>`;
      const vak = this.shadowRoot.querySelector(".kaarten");
      for (const [naam, config] of kaarten) {
        const el = document.createElement(naam);
        el.setConfig(config);
        vak.appendChild(el);
      }
    }
  }

  customElements.define("zonneburen-energieplan-card", ZonneburenEnergieplanCard);
  customElements.define("zonneburen-kwartier-card", ZonneburenKwartierCard);
  customElements.define("zonneburen-batterij-card", ZonneburenBatterijCard);
  customElements.define("zonneburen-apparaten-card", ZonneburenApparatenCard);
  customElements.define("zonneburen-buurtbatterij-card", ZonneburenBuurtbatterijCard);
  customElements.define("zonneburen-kaart-editor", ZonneburenKaartEditor);
  customElements.define("zonneburen-energieplan-panel", ZonneburenEnergieplanPanel);

  window.customCards = window.customCards || [];
  window.customCards.push(
    { type: "zonneburen-batterij-card", name: "Zonneburen: je batterij nu", description: "Wat je batterij nu doet, de lading, en knoppen om te pauzeren of het plan uit te zetten.", preview: true },
    { type: "zonneburen-energieplan-card", name: "Zonneburen: energieplan", description: "Het energieplan op een tijdlijn: prijs, lading en wat de batterij doet, met de komende blokken.", preview: true },
    { type: "zonneburen-kwartier-card", name: "Zonneburen: per kwartier", description: "Het schema van het energieplan per kwartier, zoals in de webapp. Ook goed leesbaar op je telefoon.", preview: true },
    { type: "zonneburen-apparaten-card", name: "Zonneburen: apparaten in het plan", description: "Wanneer het energieplan je boiler, auto of warmtepomp laat draaien.", preview: true },
    { type: "zonneburen-buurtbatterij-card", name: "Zonneburen: virtuele buurtbatterij", description: "De thuisbatterijen van de buurt samen, en hoeveel buren nu tegelijk delen.", preview: true },
    { type: "zonneburen-card", name: "Zonneburen: de wijk", description: "De wijk als een straat met huisjes: wie deelt, wie afneemt en wie wacht.", preview: true },
  );

  console.info(`%c☀️ ZONNEBUREN-CARD %c v${VERSIE}`,
    "background:#FFB233;color:#1b1b1b;font-weight:700;padding:2px 6px;border-radius:3px 0 0 3px",
    "background:#333;color:#fff;padding:2px 6px;border-radius:0 3px 3px 0");
})();
