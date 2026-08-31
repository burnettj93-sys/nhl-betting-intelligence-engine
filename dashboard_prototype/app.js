"use strict";
/* ==========================================================================
   NHL Engine — UX Prototype
   Static, client-side only. All DATA below is fabricated for layout review.
   No network calls, no backend, no real player/odds/result data anywhere.
   ========================================================================== */

const DEMO = true; // every render function stamps a "demo" tag using this

/* ---------------------------------------------------------------------- */
/* MOCK DATA (clearly fictional players/teams; labeled everywhere as demo) */
/* ---------------------------------------------------------------------- */

const TEAMS = ["NOR", "CST", "BAY", "PLN", "RVR", "SUM"]; // fictional 3-letter codes, not real NHL teams

const GAMES = [
  { id: "g1", away: "CST", home: "NOR", start: "7:00 PM ET", modelReady: "READY", starterReady: "READY", marketReady: "WAIT", validatedProps: 14, waitItems: 2, warnings: [] },
  { id: "g2", away: "BAY", home: "PLN", start: "7:30 PM ET", modelReady: "READY", starterReady: "WAIT", marketReady: "WAIT", validatedProps: 9, waitItems: 5, warnings: ["Starter unconfirmed"] },
  { id: "g3", away: "RVR", home: "SUM", start: "10:00 PM ET", modelReady: "WAIT", starterReady: "WAIT", marketReady: "DATA_UNAVAILABLE", validatedProps: 0, waitItems: 8, warnings: ["MoneyPuck feed stale (19h)"] },
];

const PLAYERS = [
  { id: "p1", name: "J. Fennimore", team: "CST", opp: "NOR", pos: "C", active: "PROJECTED_ACTIVE" },
  { id: "p2", name: "A. Kowalczyk", team: "NOR", opp: "CST", pos: "LW", active: "PROJECTED_ACTIVE" },
  { id: "p3", name: "T. Sandqvist", team: "BAY", opp: "PLN", pos: "D", active: "PROJECTED_ACTIVE" },
  { id: "p4", name: "M. Okafor", team: "PLN", opp: "BAY", pos: "RW", active: "PROJECTED_ACTIVE" },
  { id: "p5", name: "R. Delacroix", team: "RVR", opp: "SUM", pos: "C", active: "CONFIRMED_ACTIVE" },
  { id: "p6", name: "D. Whitfield", team: "SUM", opp: "RVR", pos: "LW", active: "PROJECTED_ACTIVE" },
];

const OPPORTUNITIES = [
  {
    id: "o1", player: "J. Fennimore", team: "CST", opp: "NOR", market: "SOG", threshold: "3+",
    rawP: 0.612, adjP: 0.612, consP: 0.571, marketNoVig: 0.548, fairOdds: "+63", currentOdds: "-105",
    maxPrice: "-118", rawEdge: 0.064, consEdge: 0.023, ev: 0.041, confidence: "HIGH", decision: "BET",
    overlay: false, drivers: ["High expected SOG volume (career-high role)", "Opponent permits elevated shot volume"],
    risks: ["Odds captured 41 min ago"], priceTs: "7:41 min ago",
  },
  {
    id: "o2", player: "A. Kowalczyk", team: "NOR", opp: "CST", market: "GOALS", threshold: "1+",
    rawP: 0.341, adjP: 0.319, consP: 0.296, marketNoVig: 0.312, fairOdds: "+213", currentOdds: "+205",
    maxPrice: "+230", rawEdge: -0.007, consEdge: -0.016, ev: -0.012, confidence: "HIGH", decision: "WATCH",
    overlay: true, overlayState: "COLD_AND_TOI_DECLINE", overlayDelta: -0.022,
    drivers: ["PP role stable", "Context overlay active (see below)"],
    risks: ["Conservative edge slightly negative after overlay"], priceTs: "12 min ago",
  },
  {
    id: "o3", player: "T. Sandqvist", team: "BAY", opp: "PLN", market: "BLOCKED_SHOTS", threshold: "2+",
    rawP: 0.447, adjP: 0.447, consP: 0.401, marketNoVig: null, fairOdds: "+149", currentOdds: null,
    maxPrice: "+162", rawEdge: null, consEdge: null, ev: null, confidence: "MEDIUM", decision: "WAIT",
    overlay: false, drivers: ["High shot-block role on PK"], risks: ["No live market posted yet"], priceTs: "NO LIVE PRICE",
  },
  {
    id: "o4", player: "M. Okafor", team: "PLN", opp: "BAY", market: "POINTS", threshold: "1+",
    rawP: 0.298, adjP: 0.256, consP: 0.231, marketNoVig: 0.288, fairOdds: "+333", currentOdds: "+240",
    maxPrice: "+310", rawEdge: -0.032, consEdge: -0.057, ev: -0.041, confidence: "MEDIUM", decision: "PASS",
    overlay: true, overlayState: "COLD_AND_TOI_DECLINE", overlayDelta: -0.042,
    drivers: [], risks: ["Context overlay active — lower expected value than market price", "Ice-time trending down 3 straight games"],
    priceTs: "5 min ago",
  },
  {
    id: "o5", player: "R. Delacroix", team: "RVR", opp: "SUM", market: "ASSISTS", threshold: "1+",
    rawP: 0.402, adjP: 0.402, consP: 0.365, marketNoVig: null, fairOdds: "+149", currentOdds: null,
    maxPrice: "+171", rawEdge: null, consEdge: null, ev: null, confidence: "LOW", decision: "DATA_UNAVAILABLE",
    overlay: false, drivers: [], risks: ["LOW confidence — capped at WATCH_ONLY by policy even if priced", "Player mapping unresolved for this book"],
    priceTs: "NO LIVE PRICE",
  },
  {
    id: "o6", player: "D. Whitfield", team: "SUM", opp: "RVR", market: "SOG", threshold: "2+",
    rawP: 0.701, adjP: 0.701, consP: 0.659, marketNoVig: 0.601, fairOdds: "-166", currentOdds: "-150",
    maxPrice: "-178", rawEdge: 0.058, consEdge: 0.058, ev: 0.037, confidence: "HIGH", decision: "BET",
    overlay: false, drivers: ["Elite shot-volume role", "Favorable matchup vs weak shot-suppression team"],
    risks: [], priceTs: "3 min ago",
  },
];

const GOALIES = [
  { id: "gl1", name: "P. Ostrowski", team: "NOR", opp: "CST", starter: "PROJECTED_STARTER", starterProb: 0.78,
    expShots: 29.4, expSaves: 26.8,
    thresholds: { "20+": "VALIDATED", "25+": "VALIDATED", "30+": "PARTIAL", "35+": "REJECTED", "40+": "INSUFFICIENT_DATA" },
    periodSupport: "P2 VALIDATED, P1/P3 PARTIAL", confidence: "HIGH",
    limitations: "Full-game 35+ shows negative historical skill; do not treat as a betting signal." },
  { id: "gl2", name: "K. Vantassel", team: "BAY", opp: "PLN", starter: "CONFIRMED_STARTER", starterProb: 0.94,
    expShots: 31.1, expSaves: 28.2,
    thresholds: { "20+": "VALIDATED", "25+": "VALIDATED", "30+": "PARTIAL", "35+": "REJECTED", "40+": "INSUFFICIENT_DATA" },
    periodSupport: "P2 VALIDATED, P1/P3 PARTIAL", confidence: "HIGH", limitations: "Backup goalie swap risk if game becomes lopsided." },
  { id: "gl3", name: "H. Ridderström", team: "SUM", opp: "RVR", starter: "UNCONFIRMED", starterProb: 0.51,
    expShots: null, expSaves: null,
    thresholds: { "20+": "VALIDATED", "25+": "VALIDATED", "30+": "PARTIAL", "35+": "REJECTED", "40+": "INSUFFICIENT_DATA" },
    periodSupport: "P2 VALIDATED, P1/P3 PARTIAL", confidence: "LOW", limitations: "Starter unconfirmed — projections withheld (WAIT) until lineup confirmation." },
];

const COMBINATIONS = [
  {
    id: "c1", legs: ["D. Whitfield SOG 2+", "K. Vantassel Saves 25+"], type: "2-leg",
    naive: 0.701 * 0.83, validated: 0.552, dependence: "structural (Poisson-mixture)", liftPct: -5.8,
    fairOdds: "-123", status: "VALIDATED", redundant: false,
  },
  {
    id: "c2", legs: ["A. Kowalczyk Goal 1+", "A. Kowalczyk Point 1+"], type: "2-leg",
    naive: 0.319 * 0.512, validated: 0.319, dependence: "exact logical identity", liftPct: null,
    fairOdds: "+213", status: "REDUNDANT", redundant: true,
    redundantNote: "Goal 1+ implies Point 1+ — these are NOT independent events. The joint probability equals P(Goal 1+) exactly, not the product of the two legs.",
  },
  {
    id: "c3", legs: ["J. Fennimore SOG 3+", "T. Sandqvist Blocks 2+", "H. Ridderström Saves 25+"], type: "3-leg",
    naive: 0.612 * 0.447 * 0.71, validated: 0.176, dependence: "Gaussian copula (data-driven winner)", liftPct: -9.1,
    fairOdds: "+468", status: "VALIDATED", redundant: false,
  },
];

const MARKET_MOVEMENT = [
  { player: "J. Fennimore", market: "SOG 3+", opening: "-108", current: "-105", modelFair: "+63", movement: "+3¢ toward model", ts: "captured 7:41 min ago", clv: "n/a — no closing line yet" },
  { player: "A. Kowalczyk", market: "Goal 1+", opening: "+198", current: "+205", modelFair: "+213", movement: "+7¢ toward model", ts: "captured 12 min ago", clv: "n/a — no closing line yet" },
  { player: "D. Whitfield", market: "SOG 2+", opening: "-140", current: "-150", modelFair: "-166", movement: "−10¢ toward model", ts: "captured 3 min ago", clv: "n/a — no closing line yet" },
];

const LEDGER = [
  { id: "l1", type: "HISTORICAL_RESEARCH", date: "2025-03-06", desc: "J. Berggren-style Goal 1+, COLD_AND_TOI_DECLINE cohort (2024-25 eval)", raw: 0.176, adj: 0.152, result: "1 (scored)" },
  { id: "l2", type: "MODEL_OBSERVATION", date: "2026-08-27", desc: "SOG 3+ prediction recorded pre-game, no market available", raw: 0.612, adj: 0.612, result: "PENDING" },
  { id: "l3", type: "SHADOW_POLICY_OBSERVATION", date: "2026-08-27", desc: "Goals 1+ context-adjusted probability logged alongside current v3 policy decision", raw: 0.341, adj: 0.319, result: "PENDING (shadow only — v3 decision unaffected)" },
  { id: "l4", type: "REAL_BET", date: "—", desc: "No real bets placed — engine is not operational for live wagering", raw: null, adj: null, result: "N/A" },
];

const MODEL_HEALTH = [
  { family: "Player SOG", status: "VALIDATED", thresholds: "1+ through 6+", prospective: "PROSPECTIVE_PENDING", confidence: "Stable across HIGH/MEDIUM/LOW", live: "SHADOW_VALIDATED", freeze: "player_sog_results.json" },
  { family: "Player Goals 1+", status: "VALIDATED", thresholds: "1+ (2+ insufficient data)", prospective: "PROSPECTIVE_PENDING", confidence: "LOW capped WATCH_ONLY", live: "SHADOW_VALIDATED", freeze: "player_goals_results.json" },
  { family: "Player Points", status: "EMPIRICAL_BASELINE_REMAINS_CHAMPION", thresholds: "1+, 2+ (3+ insufficient)", prospective: "PROSPECTIVE_PENDING", confidence: "LOW capped WATCH_ONLY", live: "SHADOW_VALIDATED", freeze: "player_points_results.json" },
  { family: "Assists", status: "VALIDATED", thresholds: "1+, 2+, 3+", prospective: "PROSPECTIVE_PENDING", confidence: "LOW capped WATCH_ONLY", live: "RESEARCH", freeze: "player_assists_results.json" },
  { family: "Blocked Shots", status: "VALIDATED", thresholds: "1+, 2+, 3+", prospective: "PROSPECTIVE_PENDING", confidence: "Normal", live: "RESEARCH", freeze: "player_blocks_results.json" },
  { family: "Team SOG", status: "VALIDATED", thresholds: "20+/25+/30+/35+ (40+ partial)", prospective: "PROSPECTIVE_PENDING", confidence: "Normal", live: "RESEARCH", freeze: "team_sog_results.json" },
  { family: "Goalie Saves", status: "PARTIAL", thresholds: "20+/25+/P2 validated; 30+/P1/P3 partial; 35+ rejected; 40+ insufficient", prospective: "PROSPECTIVE_PENDING", confidence: "Normal", live: "RESEARCH", freeze: "goalie_saves_results.json" },
  { family: "Team Goals by Period", status: "ATTEMPTED_NOT_VALIDATED", thresholds: "none", prospective: "N/A", confidence: "N/A", live: "NOT_OPERATIONAL", freeze: "team_goals_period_results.json" },
  { family: "Joint Shot/Workload", status: "VALIDATED", thresholds: "all 4 combination families", prospective: "PROSPECTIVE_PENDING", confidence: "Inherits leg-level", live: "RESEARCH", freeze: "joint_shot_workload_results.json" },
  { family: "Joint Scoring Dependence", status: "VALIDATED", thresholds: "9 of 9 combinations", prospective: "PROSPECTIVE_PENDING", confidence: "Inherits leg-level", live: "RESEARCH", freeze: "joint_scoring_dependence_results.json" },
  { family: "Context Overlay — Goals", status: "VALIDATED_OVERLAY", thresholds: "1+ (COLD_AND_TOI_DECLINE only)", prospective: "PROSPECTIVE_PENDING", confidence: "Inherits GOALS", live: "SHADOW_VALIDATED", freeze: "context_overlay_results.json" },
  { family: "Context Overlay — Points", status: "VALIDATED_OVERLAY", thresholds: "1+ (COLD_AND_TOI_DECLINE only)", prospective: "PROSPECTIVE_PENDING", confidence: "Inherits POINTS", live: "SHADOW_VALIDATED", freeze: "context_overlay_results.json" },
];

const RESEARCH_LINKS = [
  { title: "NHL Play-by-Play Corpus (4 seasons)", file: "NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md", tag: "DATA FOUNDATION" },
  { title: "Player Context State Validation", file: "PLAYER_CONTEXT_STATE_VALIDATION_REPORT.md", tag: "CONTEXT SIGNAL" },
  { title: "Context-State Probability Overlay", file: "CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md", tag: "OVERLAY" },
  { title: "Joint Shot / Workload Dependence", file: "JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md", tag: "COMBINATIONS" },
  { title: "Joint Scoring / Contribution Dependence", file: "JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md", tag: "COMBINATIONS" },
  { title: "Team Goals by Period (failed research)", file: "TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md", tag: "NOT VALIDATED" },
  { title: "Confidence Framework Redesign", file: "CONFIDENCE_FRAMEWORK_REDESIGN_REPORT.md", tag: "CONFIDENCE" },
  { title: "Goalie Saves Validation", file: "GOALIE_SAVES_VALIDATION_REPORT.md", tag: "GOALIE" },
];

/* ---------------------------------------------------------------------- */
/* Small helpers                                                          */
/* ---------------------------------------------------------------------- */

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
function pct(x, digits = 1) { return x === null || x === undefined ? "—" : (x * 100).toFixed(digits) + "%"; }
function signedPct(x, digits = 1) {
  if (x === null || x === undefined) return "—";
  const s = x >= 0 ? "+" : "";
  return s + (x * 100).toFixed(digits) + "pp";
}
function badge(text, extraClass) {
  return `<span class="badge badge-${text}${extraClass ? " " + extraClass : ""}">${text.replace(/_/g, " ")}</span>`;
}
function confPill(c) { return `<span class="pill-confidence conf-${c}">${c}</span>`; }
function tooltip(label, body) {
  return `<span class="tt">${label}<span class="tt-body">${body}</span></span>`;
}
function demoTag() { return `<span class="hist-example-tag">DEMO DATA</span>`; }

/* ---------------------------------------------------------------------- */
/* Reusable Opportunity Card component (Part 58)                          */
/* ---------------------------------------------------------------------- */

function opportunityCard(o) {
  const overlayBlock = o.overlay ? `
    <div class="notice notice-info" style="margin:0;">
      <span class="overlay-tag">${tooltip("CONTEXT ADJUSTMENT ACTIVE", "HISTORICALLY VALIDATED CONTEXT OVERLAY. Reflects recent underperformance combined with a confirmed, point-in-time-safe decline in ice time / role — not a claim about a player's mental state. Prospective validation is still PENDING for the 2026-27 season.")}</span>
      Raw ${o.market} P: <b class="mono">${pct(o.rawP)}</b> → Context Adjusted: <b class="mono">${pct(o.adjP)}</b>
      (<span class="mono">${signedPct(o.overlayDelta)}</span>) — state: <code>${o.overlayState}</code>
    </div>` : "";

  return `
  <div class="opp-card" data-id="${o.id}">
    <div class="opp-head">
      <div>
        <div class="opp-player">${o.player} <span class="dim small">(${o.team})</span></div>
        <div class="opp-meta">vs ${o.opp} · ${o.market} ${o.threshold}</div>
      </div>
      <div style="text-align:right;">
        ${badge(o.decision)}
        <div style="margin-top:4px;">${confPill(o.confidence)}</div>
      </div>
    </div>
    ${overlayBlock}
    <div class="opp-prob-row">
      <div class="opp-prob-stage"><span class="lbl">${tooltip("Raw P", "The frozen marginal model's own probability, before any context overlay, coherence, or conservative shrinkage.")}</span><span class="val">${pct(o.rawP)}</span></div>
      <div class="opp-prob-stage"><span class="lbl">${tooltip("Adjusted P", "Context-overlay-adjusted probability. Equals Raw P when no overlay applies.")}</span><span class="val ${o.overlay ? "" : "dim"}">${pct(o.adjP)}</span></div>
      <div class="opp-prob-stage"><span class="lbl">${tooltip("Conservative P", "A lower, more cautious probability estimate — a one-sided statistical haircut on small-sample confidence, not a vibe-based discount.")}</span><span class="val">${pct(o.consP)}</span></div>
      <div class="opp-prob-stage"><span class="lbl">${tooltip("Market No-Vig P", "The sportsbook's implied probability with the bookmaker margin removed. NO LIVE PRICE means no real market currently exists for this line.")}</span><span class="val">${o.marketNoVig === null ? "NO LIVE PRICE" : pct(o.marketNoVig)}</span></div>
    </div>
    <div class="opp-prob-row">
      <div class="opp-prob-stage"><span class="lbl">Fair Odds</span><span class="val price">${o.fairOdds}</span></div>
      <div class="opp-prob-stage"><span class="lbl">Current Odds</span><span class="val price">${o.currentOdds === null ? "NO LIVE PRICE" : o.currentOdds}</span></div>
      <div class="opp-prob-stage"><span class="lbl">Max Acceptable Price</span><span class="val price">${o.maxPrice}</span></div>
      <div class="opp-prob-stage"><span class="lbl">Conservative Edge / EV</span><span class="val">${o.consEdge === null ? "—" : signedPct(o.consEdge)} / ${o.ev === null ? "—" : signedPct(o.ev)}</span></div>
    </div>
    <div class="opp-footer">
      <div>
        ${o.drivers.map(d => `<div class="opp-drivers">+ ${d}</div>`).join("")}
        ${o.risks.map(r => `<div class="opp-risks">− ${r}</div>`).join("")}
      </div>
      <div class="dim small">${o.priceTs}</div>
    </div>
  </div>`;
}

/* ---------------------------------------------------------------------- */
/* Views                                                                  */
/* ---------------------------------------------------------------------- */

const root = () => document.getElementById("view-root");

function healthChip(label, status) {
  const dotClass = { OK: "dot-ok", STALE: "dot-stale", WAITING: "dot-waiting", ERROR: "dot-error", NOT_REQUIRED: "dot-nr" }[status] || "dot-nr";
  return `<span class="health-chip"><span class="health-dot ${dotClass}"></span>${label}: ${status}</span>`;
}

let TODAY_STATE = "normal"; // normal | nogames | stale

function viewToday() {
  const healthStrip = `
    <div class="health-strip">
      ${healthChip("NHL API", "OK")}
      ${healthChip("Roster Sync", "OK")}
      ${healthChip("MoneyPuck", TODAY_STATE === "stale" ? "STALE" : "OK")}
      ${healthChip("Odds API", "WAITING")}
      ${healthChip("DraftKings", "WAITING")}
      ${healthChip("Database", "OK")}
    </div>`;

  const toggles = `
    <div class="toggle-row">
      <span class="dim small" style="margin-right:6px;">Demo state:</span>
      <span class="btn ${TODAY_STATE === "normal" ? "active" : ""}" data-today-state="normal">Normal slate</span>
      <span class="btn ${TODAY_STATE === "nogames" ? "active" : ""}" data-today-state="nogames">No games today</span>
      <span class="btn ${TODAY_STATE === "stale" ? "active" : ""}" data-today-state="stale">Stale data warning</span>
    </div>`;

  let body;
  if (TODAY_STATE === "nogames") {
    body = `
      <div class="empty-state">
        <div class="icon">🏒</div>
        <div class="title">No games today</div>
        <div>The NHL schedule shows no games scheduled for this date. Check back on the next game day.</div>
      </div>`;
  } else {
    const staleWarning = TODAY_STATE === "stale" ? `
      <div class="notice notice-warn">⚠ MoneyPuck feed last synced 19 hours ago — team-context features may be stale. Player-level predictions are unaffected.</div>` : "";

    const gameCards = GAMES.map(g => `
      <div class="card" data-route="games" style="cursor:pointer;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div><b>${g.away} @ ${g.home}</b> <span class="dim small">· ${g.start}</span></div>
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">
          ${badge(g.modelReady === "READY" ? "OK" : "WAIT")} Model
          ${badge(g.starterReady === "READY" ? "OK" : "WAIT")} Starters
          ${badge(g.marketReady === "WAIT" ? "WAIT" : g.marketReady)} Market
        </div>
        <div class="small dim" style="margin-top:8px;">${g.validatedProps} validated props · ${g.waitItems} WAIT items</div>
        ${g.warnings.map(w => `<div class="notice notice-warn" style="margin-top:8px; padding:6px 10px;">${w}</div>`).join("")}
      </div>`).join("");

    const topOpps = OPPORTUNITIES.slice(0, 3).map(opportunityCard).join("");

    body = `
      ${staleWarning}
      <div class="section-title">Today's Slate ${demoTag()}</div>
      <div class="grid grid-3">${gameCards}</div>

      <div class="section-title">Top Opportunities (all decision statuses shown for UX review) ${demoTag()}</div>
      <div class="section-sub">One example each of BET / WATCH / WAIT / PASS is shown across this page and Player Props for review purposes.</div>
      <div class="grid grid-3">${topOpps}</div>
    `;
  }

  root().innerHTML = `
    <div class="topbar">
      <div class="topbar-left">
        <h1>Today</h1>
        <div class="topbar-sub">2026-08-27 (demo date) · ${TODAY_STATE === "nogames" ? "0" : GAMES.length} games</div>
      </div>
      <div class="topbar-right">${healthStrip}</div>
    </div>
    ${toggles}
    ${body}
  `;

  document.querySelectorAll("[data-today-state]").forEach(b => {
    b.addEventListener("click", () => { TODAY_STATE = b.dataset.todayState; viewToday(); });
  });
  document.querySelectorAll('.card[data-route="games"]').forEach(c => {
    c.addEventListener("click", () => navigate("games"));
  });
}

function viewGames() {
  const rows = GAMES.map(g => `
    <tr data-game="${g.id}">
      <td>${g.away} @ ${g.home}</td>
      <td class="num">${g.start}</td>
      <td>${badge(g.modelReady === "READY" ? "OK" : "WAIT")}</td>
      <td>${badge(g.starterReady === "READY" ? "OK" : "WAIT")}</td>
      <td>${badge(g.marketReady === "WAIT" ? "WAIT" : g.marketReady)}</td>
      <td class="num">${g.validatedProps}</td>
      <td class="num">${g.waitItems}</td>
      <td>${g.warnings.length ? `<span class="dim small">${g.warnings.join("; ")}</span>` : "—"}</td>
    </tr>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Games</h1><div class="topbar-sub">All games on the selected slate ${demoTag()}</div></div></div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Matchup</th><th>Start</th><th>Model</th><th>Starters</th><th>Market</th><th>Validated Props</th><th>WAIT items</th><th>Warnings</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  document.querySelectorAll("tr[data-game]").forEach(r => {
    r.addEventListener("click", () => openDrawer(gameDetailDrawer(GAMES.find(g => g.id === r.dataset.game))));
  });
}

function gameDetailDrawer(g) {
  return `
    <button class="drawer-close" data-close>&times;</button>
    <h2 style="margin-top:0;">${g.away} @ ${g.home} ${demoTag()}</h2>
    <div class="dim small">${g.start}</div>
    <div class="section-title" style="margin-top:18px;">Readiness</div>
    <div style="display:flex; gap:6px; flex-wrap:wrap;">
      ${badge(g.modelReady === "READY" ? "OK" : "WAIT")} Model
      ${badge(g.starterReady === "READY" ? "OK" : "WAIT")} Starters
      ${badge(g.marketReady === "WAIT" ? "WAIT" : g.marketReady)} Market
    </div>
    <div class="section-title">Warnings</div>
    ${g.warnings.length ? g.warnings.map(w => `<div class="notice notice-warn">${w}</div>`).join("") : '<div class="dim small">None</div>'}
    <div class="section-title">Validated Props on this slate</div>
    <div class="dim small">${g.validatedProps} validated · ${g.waitItems} WAIT — see Player Props for detail.</div>
  `;
}

/* ---- Player Props (filters + sort) ---- */

let PP_FILTERS = { market: "ALL", confidence: "ALL", decision: "ALL", validatedOnly: false, overlayOnly: false, sort: "start" };

function applyPropFilters() {
  let rows = OPPORTUNITIES.slice();
  if (PP_FILTERS.market !== "ALL") rows = rows.filter(o => o.market === PP_FILTERS.market);
  if (PP_FILTERS.confidence !== "ALL") rows = rows.filter(o => o.confidence === PP_FILTERS.confidence);
  if (PP_FILTERS.decision !== "ALL") rows = rows.filter(o => o.decision === PP_FILTERS.decision);
  if (PP_FILTERS.validatedOnly) rows = rows.filter(o => o.decision !== "DATA_UNAVAILABLE" && o.decision !== "MODEL_NOT_OPERATIONAL");
  if (PP_FILTERS.overlayOnly) rows = rows.filter(o => o.overlay);
  if (PP_FILTERS.sort === "edge") rows.sort((a, b) => (b.consEdge ?? -99) - (a.consEdge ?? -99));
  if (PP_FILTERS.sort === "ev") rows.sort((a, b) => (b.ev ?? -99) - (a.ev ?? -99));
  if (PP_FILTERS.sort === "confidence") { const order = { HIGH: 0, MEDIUM: 1, LOW: 2 }; rows.sort((a, b) => order[a.confidence] - order[b.confidence]); }
  return rows;
}

function viewPlayerProps() {
  const markets = ["ALL", ...new Set(OPPORTUNITIES.map(o => o.market))];
  const decisions = ["ALL", "BET", "WATCH", "WAIT", "PASS", "DATA_UNAVAILABLE"];

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Player Props</h1><div class="topbar-sub">Unified prop board ${demoTag()}</div></div></div>
    <div class="filter-bar">
      <div><label>Market</label><select class="select" id="f-market">${markets.map(m => `<option ${m === PP_FILTERS.market ? "selected" : ""}>${m}</option>`).join("")}</select></div>
      <div><label>Confidence</label><select class="select" id="f-conf">${["ALL", "HIGH", "MEDIUM", "LOW"].map(m => `<option ${m === PP_FILTERS.confidence ? "selected" : ""}>${m}</option>`).join("")}</select></div>
      <div><label>Decision</label><select class="select" id="f-decision">${decisions.map(m => `<option ${m === PP_FILTERS.decision ? "selected" : ""}>${m}</option>`).join("")}</select></div>
      <div><label>Sort</label><select class="select" id="f-sort">
        <option value="start" ${PP_FILTERS.sort === "start" ? "selected" : ""}>Start time</option>
        <option value="edge" ${PP_FILTERS.sort === "edge" ? "selected" : ""}>Best conservative edge</option>
        <option value="ev" ${PP_FILTERS.sort === "ev" ? "selected" : ""}>Highest EV</option>
        <option value="confidence" ${PP_FILTERS.sort === "confidence" ? "selected" : ""}>Highest confidence</option>
      </select></div>
      <label class="checkbox-inline"><input type="checkbox" id="f-validated" ${PP_FILTERS.validatedOnly ? "checked" : ""}> Validated only</label>
      <label class="checkbox-inline"><input type="checkbox" id="f-overlay" ${PP_FILTERS.overlayOnly ? "checked" : ""}> Context overlay active</label>
    </div>
    <div class="grid grid-3" id="pp-cards"></div>
  `;

  const renderCards = () => {
    const rows = applyPropFilters();
    document.getElementById("pp-cards").innerHTML = rows.length
      ? rows.map(opportunityCard).join("")
      : `<div class="empty-state" style="grid-column:1/-1;"><div class="icon">🔍</div><div class="title">No qualifying opportunities</div>Try widening your filters.</div>`;
  };
  renderCards();

  document.getElementById("f-market").addEventListener("change", e => { PP_FILTERS.market = e.target.value; renderCards(); });
  document.getElementById("f-conf").addEventListener("change", e => { PP_FILTERS.confidence = e.target.value; renderCards(); });
  document.getElementById("f-decision").addEventListener("change", e => { PP_FILTERS.decision = e.target.value; renderCards(); });
  document.getElementById("f-sort").addEventListener("change", e => { PP_FILTERS.sort = e.target.value; renderCards(); });
  document.getElementById("f-validated").addEventListener("change", e => { PP_FILTERS.validatedOnly = e.target.checked; renderCards(); });
  document.getElementById("f-overlay").addEventListener("change", e => { PP_FILTERS.overlayOnly = e.target.checked; renderCards(); });
}

function viewGoalies() {
  const cards = GOALIES.map(g => `
    <div class="card">
      <div style="display:flex; justify-content:space-between;">
        <div><b>${g.name}</b> <span class="dim small">(${g.team} vs ${g.opp})</span></div>
        ${confPill(g.confidence)}
      </div>
      <div style="margin-top:8px;">${badge(g.starter === "CONFIRMED_STARTER" ? "OK" : g.starter === "PROJECTED_STARTER" ? "WATCH" : "WAIT")} ${g.starter.replace(/_/g, " ")} · ${pct(g.starterProb)}</div>
      <div class="opp-prob-row" style="margin-top:10px;">
        <div class="opp-prob-stage"><span class="lbl">Exp. shots faced</span><span class="val">${g.expShots ?? "—"}</span></div>
        <div class="opp-prob-stage"><span class="lbl">Exp. saves</span><span class="val">${g.expSaves ?? "—"}</span></div>
      </div>
      <div class="section-title" style="font-size:12px; margin:12px 0 6px;">Validated thresholds</div>
      <div style="display:flex; gap:5px; flex-wrap:wrap;">
        ${Object.entries(g.thresholds).map(([k, v]) => `<span class="badge badge-${v}">${k} ${v.replace(/_/g, " ")}</span>`).join("")}
      </div>
      <div class="small dim" style="margin-top:8px;">${g.periodSupport}</div>
      <details class="expander"><summary>Model limitations</summary><div class="expander-body">${g.limitations}</div></details>
    </div>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Goalies</h1><div class="topbar-sub">Starter &amp; workload projections ${demoTag()}</div></div></div>
    <div class="grid grid-3">${cards}</div>
  `;
}

function viewCombinations() {
  const cards = COMBINATIONS.map(c => `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <div><b>${c.type}</b><div class="dim small">${c.legs.join(" + ")}</div></div>
        ${c.redundant ? `<span class="redundant-badge">⚠ REDUNDANT / LOGICALLY CONTAINED</span>` : badge(c.status)}
      </div>
      ${c.redundant ? `<div class="notice notice-warn">${c.redundantNote}</div>` : ""}
      <div class="opp-prob-row" style="margin-top:8px;">
        <div class="opp-prob-stage"><span class="lbl">Naive independent P</span><span class="val ${c.redundant ? "dim" : ""}">${pct(c.naive)}</span></div>
        <div class="opp-prob-stage"><span class="lbl">Validated joint P</span><span class="val">${pct(c.validated)}</span></div>
        <div class="opp-prob-stage"><span class="lbl">Dependence adj.</span><span class="val small">${c.dependence}</span></div>
        <div class="opp-prob-stage"><span class="lbl">Fair odds</span><span class="val price">${c.fairOdds}</span></div>
      </div>
      ${c.liftPct !== null ? `<div class="small dim" style="margin-top:6px;">Dependence lift vs naive: <span class="mono">${c.liftPct}%</span></div>` : ""}
      <div class="small dim" style="margin-top:6px;">No sportsbook parlay EV shown — no real combination price currently exists.</div>
    </div>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Combinations</h1><div class="topbar-sub">Validated joint-probability families only ${demoTag()}</div></div></div>
    <div class="grid grid-2">${cards}</div>
  `;
}

function viewMarketMovement() {
  const rows = MARKET_MOVEMENT.map(m => `
    <tr>
      <td>${m.player} — ${m.market}</td>
      <td class="num price">${m.opening}</td>
      <td class="num price">${m.current}</td>
      <td class="num price">${m.modelFair}</td>
      <td>${m.movement}</td>
      <td class="dim small">${m.ts}</td>
      <td class="dim small">${m.clv}</td>
    </tr>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Market Movement</h1><div class="topbar-sub">Architecture preview — no live NHL markets currently exist</div></div></div>
    <div class="notice notice-warn">DEMO / UX ONLY — line-movement tracking is not live. This view previews the intended layout for when real DraftKings snapshots are available.</div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Player / Market</th><th>Opening</th><th>Current</th><th>Model Fair</th><th>Movement</th><th>Captured</th><th>CLV</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="empty-state" style="margin-top:20px;"><div class="icon">📡</div><div class="title">WAITING FOR LIVE NHL MARKETS</div>Real opening/current/CLV tracking begins once DraftKings NHL props are posted for the 2026-27 season.</div>
  `;
}

function viewPlayers() {
  const rows = PLAYERS.map(p => `
    <tr data-player="${p.id}">
      <td>${p.name}</td><td>${p.team}</td><td>${p.pos}</td><td>vs ${p.opp}</td>
      <td>${badge(p.active === "CONFIRMED_ACTIVE" ? "OK" : "WATCH")} ${p.active.replace(/_/g, " ")}</td>
    </tr>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Players</h1><div class="topbar-sub">Click a player for full detail ${demoTag()}</div></div></div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Player</th><th>Team</th><th>Pos</th><th>Opponent</th><th>Active status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  document.querySelectorAll("tr[data-player]").forEach(r => {
    r.addEventListener("click", () => renderPlayerDetail(r.dataset.player));
  });
}

function renderPlayerDetail(id) {
  const p = PLAYERS.find(x => x.id === id);
  const opp = OPPORTUNITIES.find(o => o.player === p.name);
  const sparkVals = [1, 0, 2, 1, 3, 0, 1, 2, 1, 4];
  const spark = sparkVals.map(v => `<div class="spark-bar ${v >= 2 ? "hit" : ""}" style="height:${8 + v * 6}px;"></div>`).join("");

  root().innerHTML = `
    <div class="topbar">
      <div class="topbar-left"><h1>${p.name} ${demoTag()}</h1><div class="topbar-sub">${p.team} · ${p.pos} · vs ${p.opp}</div></div>
      <div class="topbar-right"><span class="btn" id="back-players">← Back to Players</span></div>
    </div>
    <div class="grid grid-4">
      <div class="card"><div class="card-title">Active Status</div>${badge(p.active === "CONFIRMED_ACTIVE" ? "OK" : "WATCH")}<div class="dim small" style="margin-top:6px;">Roster membership is not lineup confirmation.</div></div>
      <div class="card"><div class="card-title">Context State</div><span class="overlay-tag">COLD_AND_TOI_DECLINE</span><div class="dim small" style="margin-top:6px;">Recent underperformance + confirmed TOI decline (PIT-safe).</div></div>
      <div class="card"><div class="card-title">Confidence</div>${confPill(opp ? opp.confidence : "MEDIUM")}</div>
      <div class="card"><div class="card-title">Recent games (last 10)</div><div class="spark">${spark}</div><div class="dim small">Illustrative only — not real game logs.</div></div>
    </div>

    <div class="section-title">Prop Projections ${demoTag()}</div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Market</th><th>Threshold</th><th>Raw P</th><th>Adjusted P</th><th>Confidence</th></tr></thead>
        <tbody>
          <tr><td>SOG</td><td>3+</td><td class="num">55.4%</td><td class="num dim">55.4%</td><td>${confPill("HIGH")}</td></tr>
          <tr><td>Goals</td><td>1+</td><td class="num">${opp ? pct(opp.rawP) : "18.2%"}</td><td class="num">${opp ? pct(opp.adjP) : "15.6%"}</td><td>${confPill("HIGH")}</td></tr>
          <tr><td>Assists</td><td>1+</td><td class="num">24.1%</td><td class="num dim">24.1%</td><td>${confPill("MEDIUM")}</td></tr>
          <tr><td>Points</td><td>1+</td><td class="num">37.8%</td><td class="num dim">37.8%</td><td>${confPill("MEDIUM")}</td></tr>
          <tr><td>Blocks</td><td>1+</td><td class="num">41.0%</td><td class="num dim">41.0%</td><td>${confPill("HIGH")}</td></tr>
          <tr><td>SOG (Period 1)</td><td>1+</td><td class="num">61.9%</td><td class="num dim">61.9%</td><td>${confPill("HIGH")}</td></tr>
        </tbody>
      </table>
    </div>

    <div class="grid grid-2" style="margin-top:16px;">
      <div class="card"><div class="card-title">Model Drivers</div><div class="opp-drivers">+ Stable top-6 role, elevated recent TOI trend excluded from this cold-state read</div><div class="opp-drivers">+ Favorable opponent shot-suppression matchup</div></div>
      <div class="card"><div class="card-title">Model Risks</div><div class="opp-risks">− Ice time down 3 straight games (drives context overlay)</div><div class="opp-risks">− Starter/lineup not yet confirmed for this slate</div></div>
    </div>
    <div class="notice notice-info" style="margin-top:16px;">Context state reflects a measured production/role signal, not a claim about focus, mentality, or effort.</div>
  `;
  document.getElementById("back-players").addEventListener("click", () => navigate("players"));
}

function viewLedger() {
  const typeColor = { REAL_BET: "badge-outline", MODEL_OBSERVATION: "badge-RESEARCH", HISTORICAL_RESEARCH: "badge-PARTIAL", SHADOW_POLICY_OBSERVATION: "badge-SHADOW" };
  const rows = LEDGER.map(l => `
    <tr>
      <td><span class="badge ${typeColor[l.type]}">${l.type.replace(/_/g, " ")}</span></td>
      <td class="dim small">${l.date}</td>
      <td>${l.desc}</td>
      <td class="num">${l.raw === null ? "—" : pct(l.raw)}</td>
      <td class="num">${l.adj === null ? "—" : pct(l.adj)}</td>
      <td>${l.result}</td>
    </tr>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Bet / Observation Ledger</h1><div class="topbar-sub">Record types are never mixed in P&amp;L ${demoTag()}</div></div></div>
    <div class="notice notice-warn">No synthetic real bets are recorded here. The engine has not placed any live wagers.</div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Record type</th><th>Date</th><th>Description</th><th>Raw P</th><th>Adjusted P</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function viewModelHealth() {
  const rows = MODEL_HEALTH.map(m => `
    <tr>
      <td>${m.family}</td>
      <td>${badge(m.status)}</td>
      <td class="small">${m.thresholds}</td>
      <td class="small">${m.prospective.replace(/_/g, " ")}</td>
      <td class="small dim">${m.confidence}</td>
      <td>${badge(m.live)}</td>
      <td class="small mono dim">${m.freeze}</td>
    </tr>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Model Health</h1><div class="topbar-sub">One canonical status board — real current statuses, not demo data</div></div></div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Family</th><th>Status</th><th>Thresholds</th><th>Prospective</th><th>Confidence notes</th><th>Live readiness</th><th>Freeze file</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="footer-note">This page reflects the actual model_registry.py statuses documented in PRESEASON_ENGINE_READINESS_REPORT.md — not prototype demo data.</div>
  `;
}

function viewResearch() {
  const cards = RESEARCH_LINKS.map(r => `
    <div class="card">
      <span class="badge badge-outline">${r.tag}</span>
      <div style="margin-top:8px; font-weight:600;">${r.title}</div>
      <div class="mono dim small" style="margin-top:4px;">${r.file}</div>
    </div>`).join("");

  root().innerHTML = `
    <div class="topbar"><div class="topbar-left"><h1>Research</h1><div class="topbar-sub">Technical / validation material, separated from daily operational pages</div></div></div>
    <div class="notice notice-info">These are real report filenames from the repository root — open them directly to read full methodology, freeze manifests, and bootstrap results.</div>
    <div class="grid grid-3">${cards}</div>
  `;
}

/* ---------------------------------------------------------------------- */
/* Drawer helpers                                                         */
/* ---------------------------------------------------------------------- */

function openDrawer(html) {
  document.getElementById("drawer-content").innerHTML = html;
  document.getElementById("drawer-backdrop").classList.add("open");
  document.querySelector("[data-close]").addEventListener("click", closeDrawer);
}
function closeDrawer() { document.getElementById("drawer-backdrop").classList.remove("open"); }
document.getElementById("drawer-backdrop").addEventListener("click", e => { if (e.target.id === "drawer-backdrop") closeDrawer(); });

/* ---------------------------------------------------------------------- */
/* Router                                                                 */
/* ---------------------------------------------------------------------- */

const ROUTES = {
  today: viewToday, games: viewGames, "player-props": viewPlayerProps, goalies: viewGoalies,
  combinations: viewCombinations, "market-movement": viewMarketMovement, players: viewPlayers,
  ledger: viewLedger, "model-health": viewModelHealth, research: viewResearch,
};

function navigate(route) {
  if (!ROUTES[route]) route = "today";
  window.location.hash = route;
  document.querySelectorAll(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.route === route));
  ROUTES[route]();
  window.scrollTo(0, 0);
}

document.querySelectorAll(".nav-item").forEach(item => {
  item.addEventListener("click", () => navigate(item.dataset.route));
  item.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(item.dataset.route); }
  });
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

window.addEventListener("hashchange", () => navigate(window.location.hash.replace("#", "")));

const initialRoute = window.location.hash.replace("#", "") || "today";
navigate(initialRoute);
