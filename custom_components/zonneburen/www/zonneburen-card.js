/*
 * ☀️ Zonneburen Card v1.5.0
 * Custom Lovelace card voor het Zonneburen Enterprise EMS.
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

  const VERSIE = "1.5.0";
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

    setConfig(config) {
      if (!config || !config.entity) {
        throw new Error("Zonneburen Card: 'entity' is verplicht (bijv. sensor.zonneburen_wijk).");
      }
      this._config = {
        titel: "Zonneburen",
        compact: false,
        mijn_adres: null,
        ...config,
      };
      this._laatsteRender = null;
    }

    static getStubConfig() {
      return { entity: "sensor.zonneburen_wijk", mijn_adres: "" };
    }

    getCardSize() {
      return this._config && this._config.compact ? 3 : 5;
    }

    set hass(hass) {
      this._hass = hass;
      const st = hass.states[this._config.entity];
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
          detail: { entityId: this._config.entity },
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
              <span class="melding-titel">Sensor niet gevonden</span>
              <span>"${esc(cfg.entity)}" bestaat (nog) niet. Controleer of de wijk-server de
              MQTT Discovery-berichten heeft gepubliceerd, en of de MQTT-integratie draait.</span>
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
      const mijnHuis = cfg.mijn_adres ? huizen.find((h) => h && h.adres === cfg.mijn_adres) : null;
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
            const isMij = cfg.mijn_adres && h.adres === cfg.mijn_adres;
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

  // ------------------------------------------------------------------ Energieplan

  const PLAN_ZIN = {
    zelfverbruik: "Eigen stroom gebruiken", netladen: "Goedkoop bijladen van het net",
    netontladen: "Terugleveren uit de batterij", vasthouden: "Lading bewaren voor later",
    alleenzon: "Alleen zon opslaan", standby: "Batterij rust",
  };
  const PLAN_KLEUR = {
    netladen: "var(--info-color, #0284c7)", netontladen: "var(--success-color, #059669)",
    zelfverbruik: "var(--success-color, #059669)", vasthouden: "var(--disabled-text-color, #9aa1ad)",
    alleenzon: "var(--warning-color, #f59e0b)", standby: "var(--disabled-text-color, #9aa1ad)",
  };
  const tijd = (unix) => new Date(unix * 1000).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
  const euro = (v) => "€ " + Number(v).toFixed(2).replace(".", ",");
  // "02:15", of "morgen 02:15" als het niet meer vandaag is.
  const dagTijd = (unix) => {
    const d = new Date(unix * 1000), vandaag = new Date();
    const dagen = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - new Date(vandaag.getFullYear(), vandaag.getMonth(), vandaag.getDate())) / 864e5);
    return (dagen === 1 ? "morgen " : dagen > 1 ? "overmorgen " : "") + tijd(unix);
  };

  class ZonneburenEnergieplanCard extends HTMLElement {
    setConfig(config) {
      if (!config || !config.entity) throw new Error("Zonneburen Energieplan: 'entity' is verplicht (de sensor Energieplan).");
      this._config = config;
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    }

    static getStubConfig() { return { entity: "" }; }

    getCardSize() { return 6; }

    set hass(hass) {
      this._hass = hass;
      const st = hass.states[this._config.entity];
      const sleutel = st ? st.last_updated + "|" + st.state : "leeg";
      if (sleutel === this._vorige) return;
      this._vorige = sleutel;
      this._render(st);
    }

    _render(st) {
      if (!st) {
        this.shadowRoot.innerHTML = `<ha-card><div class="leeg">"${esc(this._config.entity)}" bestaat niet. Kies de sensor Energieplan van de Zonneburen-integratie.</div></ha-card>${this._stijl()}`;
        return;
      }
      const a = st.attributes || {};
      const kwartieren = Array.isArray(a.kwartieren) ? a.kwartieren : [];
      const blokken = Array.isArray(a.blokken) ? a.blokken : [];
      const besparingId = this._config.entity.replace(/energieplan$/, "verwachte_besparing");
      const besparing = this._hass && this._hass.states[besparingId] ? Number(this._hass.states[besparingId].state) : NaN;
      const pauze = a.pauzeTot && a.pauzeTot * 1000 > Date.now();

      let tijdlijn = "";
      if (kwartieren.length > 1) {
        const B = 600, H = 120, L = 4, R = 4;
        const t0 = kwartieren[0][0], t1 = kwartieren[kwartieren.length - 1][0] + 900;
        const x = (t) => L + (t - t0) / (t1 - t0) * (B - L - R);
        const prijzen = kwartieren.map((k) => k[3]).filter((p) => typeof p === "number").sort((p, q) => p - q);
        const laag = prijzen[Math.floor(prijzen.length / 10)] ?? 0, hoog = prijzen[Math.floor(prijzen.length * 9 / 10)] ?? 1;
        const prijs = kwartieren.map((k) => {
          const f = hoog - laag > 0.0001 ? Math.min(1, Math.max(0, (k[3] - laag) / (hoog - laag))) : 0.5;
          return `<rect x="${x(k[0]).toFixed(1)}" y="4" width="${(x(k[0] + 900) - x(k[0]) + 0.3).toFixed(1)}" height="12" fill="var(--error-color, #dc2626)" opacity="${(0.08 + f * 0.6).toFixed(2)}"><title>${tijd(k[0])} · ${typeof k[3] === "number" ? euro(k[3]) : "–"} per kWh</title></rect>`;
        }).join("");
        const soc = kwartieren.map((k, i) => `${i ? "L" : "M"}${x(k[0] + 900).toFixed(1)} ${(100 - k[2] * 0.7).toFixed(1)}`).join(" ");
        const standen = kwartieren.map((k) => `<rect x="${x(k[0]).toFixed(1)}" y="${H - 12}" width="${(x(k[0] + 900) - x(k[0]) + 0.3).toFixed(1)}" height="8" fill="${PLAN_KLEUR[k[1]] || "#999"}"><title>${tijd(k[0])}: ${esc(PLAN_ZIN[k[1]] || k[1])}</title></rect>`).join("");
        const tikken = [];
        for (let t = Math.ceil(t0 / 21600) * 21600; t < t1; t += 21600) tikken.push(`<text x="${x(t).toFixed(1)}" y="${H + 12}" text-anchor="middle">${tijd(t)}</text>`);
        tijdlijn = `<svg viewBox="0 0 ${B} ${H + 16}" class="tijdlijn" role="img" aria-label="Tijdlijn van het energieplan">
          ${prijs}
          <line x1="${L}" x2="${B - R}" y1="100" y2="100" class="nul"/>
          <path d="${soc}" class="soc"/>
          ${standen}${tikken.join("")}
        </svg>`;
      }

      // Apparaten: per apparaat een baan onder de tijdlijn en een regel in de lijst.
      const app = Array.isArray(a.apparaatblokken) ? a.apparaatblokken : [];
      let banen = "";
      if (app.length && kwartieren.length > 1) {
        const t0 = kwartieren[0][0], t1 = kwartieren[kwartieren.length - 1][0] + 900;
        const pct = (t) => Math.min(100, Math.max(0, (t - t0) / (t1 - t0) * 100));
        const namen = [...new Set(app.map((b) => b.naam))];
        banen = `<div class="banen">${namen.map((n) => `<div class="baan"><span>${esc(n)}</span><div class="spoor">${app.filter((b) => b.naam === n).map((b) => {
          const kleur = b.stand === "blokkeren" ? "var(--disabled-text-color, #9aa1ad)" : b.stand === "laden" ? "var(--info-color, #0284c7)" : "var(--warning-color, #f59e0b)";
          return `<i style="left:${pct(b.van).toFixed(2)}%;width:${Math.max(0.6, pct(b.tot) - pct(b.van)).toFixed(2)}%;background:${kleur}" title="${esc(b.naam)} · ${dagTijd(b.van)}–${tijd(b.tot)} · ${esc(b.zin)}"></i>`;
        }).join("")}</div></div>`).join("")}</div>`;
      }
      const komend = (this._config.apparaten === false ? [] : app).slice(0, 8);
      const appLijst = komend.map((b) => `<li style="border-left-color:${b.stand === "blokkeren" ? "var(--disabled-text-color,#9aa1ad)" : "var(--warning-color,#f59e0b)"}"><b>${dagTijd(b.van)}–${tijd(b.tot)}</b><span><strong>${esc(b.naam)}</strong> · ${esc(b.zin)}${b.i === null || b.i === undefined ? " <small>(zelf aanzetten)</small>" : ""}</span></li>`).join("");
      const geldig = a.geldig_tot ? `<div class="voet">Dit plan staat op ${a.soort === "hub" ? "je hub" : "Home Assistant"} en loopt door tot ${new Date(a.geldig_tot * 1000).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit" })}, ook zonder internet.</div>` : "";

      const lijst = blokken.map((b) => `<li style="border-left-color:${PLAN_KLEUR[b.stand] || "#999"}"><b>${dagTijd(b.van)}–${tijd(b.tot)}</b><span>${esc(b.zin)}</span></li>`).join("");

      this.shadowRoot.innerHTML = `
        <ha-card>
          <div class="kop">
            <div>
              <div class="titel">${esc(this._config.titel || "Energieplan")}</div>
              <div class="sub">${pauze ? "Gepauzeerd tot " + tijd(a.pauzeTot) : a.gestart ? "Stuurt je batterij" : a.actief ? "Volgt het plan" : "Alleen advies"}</div>
            </div>
            ${isFinite(besparing) ? `<div class="bedrag">${euro(Math.max(0, besparing))}<small>verwachte besparing</small></div>` : ""}
          </div>
          <div class="nu">Nu: ${esc(st.state)}${a.afwijking ? ` <small>(${esc(a.afwijking)})</small>` : ""}</div>
          ${a.geweigerd ? `<div class="melding">Plan geweigerd: ${esc(a.geweigerd)}</div>` : ""}
          ${a.fout ? `<div class="melding">${esc(a.fout)}</div>` : ""}
          ${tijdlijn}
          <div class="legenda"><span><i style="background:var(--info-color,#0284c7)"></i>Bijladen</span><span><i style="background:var(--success-color,#059669)"></i>Uit de batterij</span><span><i class="lijn"></i>Batterij %</span><span><i style="background:var(--error-color,#dc2626);opacity:.5"></i>Prijs</span></div>
          ${banen}
          ${lijst ? `<h3>Batterij</h3><ul class="blokken">${lijst}</ul>` : ""}
          ${appLijst ? `<h3>Apparaten</h3><ul class="blokken">${appLijst}</ul>` : ""}
          ${geldig}
        </ha-card>${this._stijl()}`;
    }

    _stijl() {
      return `<style>
        ha-card { padding: 16px; }
        .leeg { color: var(--secondary-text-color); }
        .kop { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
        .titel { font-size: 1.15rem; font-weight: 700; color: var(--primary-text-color); }
        .sub { color: var(--secondary-text-color); font-size: .85rem; }
        .bedrag { font-size: 1.6rem; font-weight: 800; text-align: right; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
        .bedrag small { display: block; font-size: .75rem; font-weight: 500; color: var(--secondary-text-color); }
        .nu { margin: 10px 0; font-weight: 600; color: var(--primary-text-color); }
        .nu small { font-weight: 400; color: var(--secondary-text-color); }
        .melding { margin: 6px 0; padding: 8px 10px; border-radius: 8px; background: rgba(220, 38, 38, .1); font-size: .85rem; }
        .tijdlijn { width: 100%; height: auto; display: block; margin-top: 6px; }
        .tijdlijn text { font-size: 11px; fill: var(--secondary-text-color); }
        .tijdlijn .soc { fill: none; stroke: var(--success-color, #059669); stroke-width: 2; stroke-dasharray: 5 4; }
        .tijdlijn .nul { stroke: var(--divider-color); }
        .legenda { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: .78rem; color: var(--secondary-text-color); margin-top: 6px; }
        .legenda i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
        .legenda i.lijn { height: 2px; background: var(--success-color, #059669); vertical-align: 3px; }
        .blokken { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 6px; }
        .blokken li { display: grid; grid-template-columns: 9.5rem 1fr; gap: 10px; padding: 8px 10px; border-radius: 8px; background: var(--secondary-background-color); border-left: 4px solid; font-size: .9rem; }
        .blokken b { font-variant-numeric: tabular-nums; }
        .blokken small { color: var(--secondary-text-color); }
        .banen { margin-top: 10px; display: grid; gap: 4px; }
        .baan { display: grid; grid-template-columns: 6.5rem 1fr; align-items: center; gap: 8px; font-size: .8rem; color: var(--secondary-text-color); }
        .baan span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .spoor { position: relative; height: 12px; border-radius: 6px; background: var(--secondary-background-color, rgba(127,127,127,.12)); }
        .spoor i { position: absolute; top: 0; bottom: 0; border-radius: 6px; }
        h3 { margin: 14px 0 0; font-size: .8rem; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); }
        .voet { margin-top: 12px; font-size: .78rem; color: var(--secondary-text-color); }
      </style>`;
    }
  }

  customElements.define("zonneburen-energieplan-card", ZonneburenEnergieplanCard);

  // ------------------------------------------------------------------ Paneel in de zijbalk

  // Het paneel Energieplan dat de integratie registreert: zoekt zelf de sensoren van Zonneburen,
  // dus de bewoner hoeft niets in te stellen. Eén plan per huis, plus een tegel per apparaat.
  class ZonneburenEnergieplanPanel extends HTMLElement {
    set hass(hass) {
      this._hass = hass;
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
      const plannen = Object.keys(hass.states).filter((id) => id.startsWith("sensor.") && Array.isArray(hass.states[id].attributes.kwartieren));
      const apparaten = Object.keys(hass.states).filter((id) => id.startsWith("sensor.") && "volgt_plan" in hass.states[id].attributes);
      const sleutel = plannen.join() + "|" + apparaten.join() + "|" + this.narrow;
      if (sleutel !== this._sleutel) {
        this._sleutel = sleutel;
        this._bouw(plannen, apparaten);
      }
      this.shadowRoot.querySelectorAll("zonneburen-energieplan-card").forEach((k) => { k.hass = hass; });
      const menu = this.shadowRoot.querySelector("ha-menu-button");
      if (menu) { menu.hass = hass; menu.narrow = this.narrow; }
      this._tegels(apparaten);
    }

    set narrow(n) { this._narrow = n; }
    get narrow() { return !!this._narrow; }

    _bouw(plannen, apparaten) {
      const leeg = !plannen.length
        ? `<div class="leeg"><b>Nog geen energieplan.</b><br>Zet in de webapp bij Energieplan de stand op Adviseren of Sturen. Het plan komt binnen zodra Home Assistant met Zonneburen verbonden is, en blijft daarna 36 uur staan, ook zonder internet.</div>`
        : "";
      this.shadowRoot.innerHTML = `
        <div class="balk"><ha-menu-button></ha-menu-button><span>Energieplan</span></div>
        <div class="inhoud">
          ${leeg}
          ${plannen.map((id) => `<zonneburen-energieplan-card data-id="${esc(id)}"></zonneburen-energieplan-card>`).join("")}
          ${apparaten.length ? `<h2>Apparaten in het plan</h2><div class="tegels"></div>` : ""}
        </div>
        <style>
          :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color); }
          .balk { display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px); padding: 0 12px; font-size: 20px;
                  background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); }
          .inhoud { max-width: 960px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
          .leeg { padding: 24px; border-radius: 12px; background: var(--card-background-color); line-height: 1.5; }
          h2 { margin: 8px 0 0; font-size: 1rem; font-weight: 600; }
          .tegels { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
          .tegel { padding: 14px 16px; border-radius: 12px; background: var(--card-background-color); box-shadow: var(--ha-card-box-shadow, none);
                   border: 1px solid var(--divider-color); }
          .tegel b { display: block; margin-bottom: 4px; }
          .tegel span { color: var(--secondary-text-color); font-size: .9rem; }
          .tegel.aan { border-color: var(--warning-color, #f59e0b); }
        </style>`;
      this.shadowRoot.querySelectorAll("zonneburen-energieplan-card").forEach((k) => {
        const id = k.dataset.id;
        const naam = (this._hass.states[id].attributes.friendly_name || "Energieplan").replace(/ Energieplan$/, "");
        k.setConfig({ entity: id, titel: plannen.length > 1 ? naam : "Energieplan", apparaten: true });
      });
    }

    _tegels(apparaten) {
      const vak = this.shadowRoot.querySelector(".tegels");
      if (!vak) return;
      const html = apparaten.map((id) => {
        const st = this._hass.states[id];
        const naam = (st.attributes.friendly_name || id).replace(/^.*Energieplan /, "");
        return `<div class="tegel${st.attributes.volgt_plan ? " aan" : ""}"><b>${esc(naam)}</b><span>${esc(st.state)}</span></div>`;
      }).join("");
      if (html !== this._tegelHtml) { this._tegelHtml = html; vak.innerHTML = html; }
    }
  }

  customElements.define("zonneburen-energieplan-panel", ZonneburenEnergieplanPanel);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "zonneburen-card",
    name: "Zonneburen Card",
    description: "Live wijkvisualisatie van het Zonneburen P2P-energiesysteem.",
    preview: true,
  });
  window.customCards.push({
    type: "zonneburen-energieplan-card",
    name: "Zonneburen Energieplan",
    description: "Het energieplan van je eigen huis: wat je batterij de komende uren doet en waarom.",
  });

  console.info(`%c☀️ ZONNEBUREN-CARD %c v${VERSIE}`,
    "background:#FFB233;color:#1b1b1b;font-weight:700;padding:2px 6px;border-radius:3px 0 0 3px",
    "background:#333;color:#fff;padding:2px 6px;border-radius:0 3px 3px 0");
})();
