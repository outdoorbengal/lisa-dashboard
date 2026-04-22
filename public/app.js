// app.js — dashboard rendering, modal interactions, sprint actions.
// Depends on auth.js being loaded first.

// ─────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────
function pct(w, t) { if (!t) return null; return Math.round((w / t) * 100); }

function donutDash(p, r) {
  const c = 2 * Math.PI * r;
  if (p === null) return { win: "0 " + c, lose: "0 " + c, wo: -c * 0.25, lo: -c * 0.25 };
  const wa = (p / 100) * c, la = ((100 - p) / 100) * c, off = c * 0.25;
  return { win: `${wa} ${c - wa}`, lose: `${la} ${c - la}`, wo: -off, lo: -(off + wa) };
}

function bCls(s) { return { RUNNING: "b-run", WINNER: "b-win", NEUTRAL: "b-neu", LOSER: "b-los" }[s] || "b-neu"; }

function fmtVal(val, display, unit) {
  if (display) return display;
  if (val == null) return "—";
  return unit === "count" ? val.toLocaleString() : val + "%";
}
function bLbl(e) { return e.status === "RUNNING" ? `WK ${e.week_current || "?"} / ${e.week_total || "?"}` : e.status; }
function tlCls(s) { return { RUNNING: "f-run", WINNER: "f-done-w", LOSER: "f-done-l" }[s] || "f-done-n"; }
function fCls(s) { return { WINNER: "f-win", LOSER: "f-los" }[s] || "f-neu"; }
function dCls(d) { if (!d) return "d-f"; return d.startsWith("+") ? "d-p" : d.startsWith("-") ? "d-n" : "d-f"; }

function parseMonDD(str) {
  if (!str) return { mon: "", day: "" };
  const p = str.split(" ");
  return p.length === 2 ? { mon: p[0].toUpperCase(), day: p[1] } : { mon: "", day: str };
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showToast(msg, kind = "") {
  const el = document.getElementById("toast");
  el.className = "toast " + kind;
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add("hidden"), 3000);
}

window.showToast = showToast;

// ─────────────────────────────────────────────
// Data loading
// ─────────────────────────────────────────────
async function loadData() {
  const resp = await fetch(`./data.json?t=${Date.now()}`, { cache: "no-store" });
  if (!resp.ok) throw new Error("Could not load data.json");
  return resp.json();
}

// ─────────────────────────────────────────────
// Main render
// ─────────────────────────────────────────────
let currentData = null;

function render(d) {
  currentData = d;
  const sys = d.system;
  const days = sys.scan_day_labels || ["M", "T", "W", "T", "F", "S", "Su"];
  const ti = sys.today_index !== undefined ? sys.today_index : new Date().getDay() === 0 ? 6 : new Date().getDay() - 1;

  const scanBars = (sys.scan_bars || []).map((h, i) => {
    const cls = i === ti ? "today" : h > 0 ? "done" : "future";
    return `<div class="sbar ${cls}" style="height:${Math.max(h, 4)}%"></div>`;
  }).join("");
  const scanLbls = days.map((l) => `<span>${l}</span>`).join("");

  const dtypes = [
    { key: "total", label: "Total", f: true },
    { key: "rnk", label: "RNK" },
    { key: "ctr", label: "CTR" },
    { key: "cr", label: "CR" },
    { key: "link", label: "LINK" },
  ];
  const donuts = dtypes.map((dt) => {
    const dd = d.donuts[dt.key] || { win: 0, total: 0 };
    const p = pct(dd.win, dd.total), r = 26, dash = donutDash(p, r);
    const col = p === null ? "var(--muted)" : p >= 50 ? "var(--teal)" : "var(--orange)";
    return `<div class="dcard${dt.f ? " featured" : ""}">
      <div class="d-lbl">${dt.label}</div>
      <svg class="d-svg" viewBox="0 0 72 72">
        <circle cx="36" cy="36" r="${r}" fill="none" stroke="var(--border)" stroke-width="8"/>
        ${p !== null && p > 0 ? `<circle cx="36" cy="36" r="${r}" fill="none" stroke="${col}" stroke-width="8" stroke-dasharray="${dash.win}" stroke-dashoffset="${dash.wo}" stroke-linecap="round"/>` : ""}
        ${p !== null && p < 100 && dd.total > 0 ? `<circle cx="36" cy="36" r="${r}" fill="none" stroke="var(--orange)" stroke-width="8" stroke-dasharray="${dash.lose}" stroke-dashoffset="${dash.lo}" stroke-linecap="round"/>` : ""}
      </svg>
      <div class="d-pct" style="color:${col}">${p === null ? "—" : p + "%"}</div>
      <div class="d-sub">${p === null ? "no data" : "winning"}</div>
      <div class="d-count">${dd.win} of ${dd.total}</div>
    </div>`;
  }).join("");

  const exps = (d.experiments || []).map((exp) => {
    const run = exp.status === "RUNNING", ha = exp.after_val != null;
    const sd = parseMonDD(exp.start_date);
    const afterRow = run
      ? `<div class="ba-row"><div class="ba-tag">Target</div><div class="ba-track"><div class="ba-fill f-target" style="width:${exp.target_pct || 0}%"></div></div><div class="ba-val">${fmtVal(exp.target_val, exp.target_display, exp.unit)}</div></div>`
      : `<div class="ba-row"><div class="ba-tag" style="color:${exp.status === "WINNER" ? "var(--teal)" : "var(--text2)"}">After</div><div class="ba-track"><div class="ba-fill ${fCls(exp.status)}" style="width:${exp.after_pct || 0}%"></div></div><div class="ba-val" style="color:${exp.status === "WINNER" ? "var(--teal)" : "var(--text2)"}">${ha ? fmtVal(exp.after_val, exp.after_display, exp.unit) : "—"}</div></div>`;
    const deltaHtml = exp.delta ? `<div class="delta-tag ${dCls(exp.delta)}">${exp.delta}</div>` : run ? `<div class="delta-tag d-f">TBD</div>` : "";
    return `<div class="exp-row" data-exp-id="${escapeHtml(exp.id)}">
      <div class="exp-date-blk"><div class="exp-date-mon">${sd.mon}</div><div class="exp-date-num">${sd.day}</div></div>
      <div class="exp-body">
        <div class="exp-name">${escapeHtml(exp.name)}</div>
        <div class="exp-kpi-line">${escapeHtml(exp.kpi_label)}</div>
        <div class="ba-row-wrap">
          <div class="ba-row"><div class="ba-tag">Before</div><div class="ba-track"><div class="ba-fill f-before" style="width:${exp.before_pct}%"></div></div><div class="ba-val">${fmtVal(exp.before_val, exp.before_display, exp.unit)}</div></div>
          ${afterRow}
        </div>
        <div class="tl-wrap">
          <div class="tl-track"><div class="tl-fill ${tlCls(exp.status)}" style="width:${exp.progress_pct}%"></div></div>
          <div class="tl-lbls"><span>${exp.start_date}</span><span style="color:${exp.status==="WINNER"?"var(--teal)":"var(--muted)"};font-weight:500">${escapeHtml(exp.note||"")}</span><span>${exp.end_date}</span></div>
        </div>
      </div>
      <div class="exp-right"><span class="e-badge ${bCls(exp.status)}">${bLbl(exp)}</span>${deltaHtml}</div>
    </div>`;
  }).join("") || '<div class="empty">No experiments yet</div>';

  const activeCount = (d.experiments || []).filter((e) => e.status === "RUNNING").length;
  const winRate = pct(d.donuts.total.win, d.donuts.total.total);
  const pendingCount = (d.sprints || []).filter((s) => s.status === "UNACKNOWLEDGED").length;

  const sprints = (d.sprints || []).slice(0, 10).map((sp) => {
    const unack = sp.status === "UNACKNOWLEDGED";
    const next  = sp.status === "NEXT_UP";
    const effortCls = { LOW:"effort-low", MEDIUM:"effort-medium", HIGH:"effort-high" }[sp.effort_level] || "effort-medium";
    const badge = unack
      ? `<span class="s-unack">${sp.days_waiting}d waiting</span>`
      : next
        ? `<span class="s-next">NEXT UP</span>`
        : `<span class="s-queued">#${sp.queue_position || ""}</span>`;
    return `<div class="sprint-row" data-sprint-id="${escapeHtml(sp.id)}">
      <div class="sprint-date-blk"><div class="sprint-num-lbl">Sprint</div><div class="sprint-num-big">#${escapeHtml(sp.id)}</div></div>
      <div class="sprint-body">
        <div class="sprint-title">${escapeHtml(sp.title)}</div>
        <div class="sprint-url">${escapeHtml(sp.url)}</div>
        <div class="sprint-meta">
          <span class="sprint-kpi-pill">${escapeHtml(sp.impact_label || sp.sprint_type || "")}</span>
          ${sp.effort_level ? `<span class="sprint-effort-pill ${effortCls}">${escapeHtml(sp.effort_level)}</span>` : ""}
          ${sp.effort_minutes ? `<span class="sprint-kpi-pill">~${sp.effort_minutes}min</span>` : ""}
        </div>
      </div>
      <div class="sprint-right">${badge}</div>
    </div>`;
  }).join("") || '<div class="empty">No pending sprints</div>';

  const auth = Auth.current;
  const userBlock = auth
    ? `<div class="user-block">
        <img class="user-avatar" src="${escapeHtml(auth.user.avatar_url)}" alt="${escapeHtml(auth.user.login)}">
        <span class="user-name">${escapeHtml(auth.user.login)}</span>
        <button class="logout-btn" id="logout-btn">Sign out</button>
      </div>`
    : "";

  document.getElementById("app").innerHTML = `
    <div class="page">
      <div class="page-header">
        <div>
          <div class="page-title">Lisa Dashboard</div>
          <div class="page-sub">SEO & growth agent · OutdoorBengal.com</div>
          <div class="page-meta">
            <div class="status-pill"><div class="hdot"></div>${escapeHtml(sys.status || "Running")}</div>
            <span class="page-date">${escapeHtml(d.week_of)}</span>
          </div>
        </div>
        ${userBlock}
      </div>

      <div class="section"><div class="stat-grid">
        <div class="stat-card"><div class="stat-icon" style="background:#eff6ff">📄</div><div><div class="stat-val">${sys.pages_scanned}<span> / ${sys.pages_total}</span></div><div class="stat-lbl">Pages Scanned</div><div class="stat-note" style="color:var(--teal)">This week</div></div></div>
        <div class="stat-card"><div class="stat-icon" style="background:#f0fdf4">🔗</div><div><div class="stat-val">${sys.sources_connected}<span> / ${sys.sources_total}</span></div><div class="stat-lbl">Data Sources</div><div class="stat-note" style="color:var(--green)">All connected</div></div></div>
        <div class="stat-card"><div class="stat-icon" style="background:#eff6ff">🧪</div><div><div class="stat-val">${activeCount}</div><div class="stat-lbl">Active Tests</div><div class="stat-note" style="color:var(--muted2)">Running now</div></div></div>
        <div class="stat-card"><div class="stat-icon" style="background:${pendingCount > 0 ? "var(--orange-lt)" : "var(--green-lt)"}">${pendingCount > 0 ? "⚠️" : "✅"}</div><div><div class="stat-val" style="color:${pendingCount > 0 ? "var(--orange)" : "var(--green)"}">${pendingCount}</div><div class="stat-lbl">Pending Sprints</div><div class="stat-note" style="color:${pendingCount > 0 ? "var(--orange)" : "var(--muted2)"}">${pendingCount > 0 ? "Needs response" : "All acknowledged"}</div></div></div>
      </div></div>

      <div class="section"><div class="card"><div class="scan-wrap"><div class="scan-inner">
        <div><div class="scan-lbl-title">Weekly Scan</div><div class="scan-lbl-sub">Pages per day</div></div>
        <div class="scan-chart"><div class="scan-bars">${scanBars}</div><div class="sbar-lbls">${scanLbls}</div></div>
        <div class="scan-stat"><div class="scan-big">${sys.days_complete || 0}/7</div><div class="scan-sm">days complete</div></div>
      </div></div></div></div>

      <div class="section"><div class="sec-header"><div class="sec-title-text">Experiment Win Rate</div></div>
        <div class="donut-grid">${donuts}</div>
      </div>

      <div class="section"><div class="card">
        <div class="card-header">
          <div class="card-header-left"><div class="sec-icon" style="background:var(--teal-lt)">🧪</div><div><div class="card-title">Experiments</div><div class="card-sub">Click any row for full details</div></div></div>
          ${winRate !== null ? `<div class="card-badge" style="background:var(--teal-lt);color:var(--teal);border:1px solid var(--teal-md)">${winRate}% win rate</div>` : ""}
        </div>
        ${exps}
      </div></div>

      <div class="section"><div class="card">
        <div class="card-header">
          <div class="card-header-left"><div class="sec-icon" style="background:var(--orange-lt)">⚡</div><div><div class="card-title">Sprints</div><div class="card-sub">Click any sprint for full brief &amp; instructions</div></div></div>
          ${pendingCount > 0 ? `<div class="card-badge" style="background:var(--orange-lt);color:var(--orange);border:1px solid #fca5a5">${pendingCount} unacknowledged</div>` : ""}
        </div>
        <div class="sprint-list">${sprints}</div>
      </div></div>

      <footer>
        <span>Lisa v2 · outdoorbengal.com</span>
        <span>
          <button id="log-btn">View run log</button>
           · ${sys.pages_total} pages · ${escapeHtml(d.generated)}
        </span>
      </footer>
    </div>
  `;

  wireEvents();
  document.getElementById("loading").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
}

// ─────────────────────────────────────────────
// Event wiring
// ─────────────────────────────────────────────
function wireEvents() {
  // Logout
  document.getElementById("logout-btn")?.addEventListener("click", () => {
    Auth.clear();
    location.reload();
  });

  // Log viewer
  document.getElementById("log-btn")?.addEventListener("click", openLogModal);

  // Sprint detail — whole row clickable
  document.querySelectorAll(".sprint-row[data-sprint-id]").forEach((el) => {
    el.addEventListener("click", () => openSprintDetail(el.dataset.sprintId));
  });

  // Experiment detail
  document.querySelectorAll("[data-exp-id]").forEach((el) => {
    el.addEventListener("click", (e) => openExperimentDetail(e.currentTarget.dataset.expId));
  });

  // Action buttons
  document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const action = btn.dataset.action;
      const id = btn.dataset.sprintId;
      handleAction(action, id);
    });
  });
}

// ─────────────────────────────────────────────
// Sprint detail modal
// ─────────────────────────────────────────────
function findSprintInDetails(id) {
  const details = currentData?.details || {};
  return (details.queue || []).find((s) => s.id === id);
}

function findExperimentInDetails(id) {
  const details = currentData?.details || {};
  return (details.active || []).find((e) => e.id === id) || (details.archive || []).find((e) => e.id === id);
}

function buildBriefSteps(sprint) {
  const type = sprint.sprint_type || "RNK";
  const url = sprint.url || "";
  const effort = sprint.effort || {};
  const kpi = sprint.kpi || {};
  let steps = [];
  if (type === "RNK" || type === "CTR") {
    steps = [
      { n:1, text:`Go to <strong>Shopify &rarr; Online Store</strong> and open the page at <code>${url}</code>` },
      { n:2, text:`Click <strong>Edit SEO</strong> at the bottom of the page editor.` },
      { n:3, text:`<strong>Page title (max 60 chars):</strong> Lead with the primary keyword, add a specific benefit. Do not use the URL slug. Sound human.` },
      { n:4, text:`<strong>Meta description (max 155 chars):</strong> Expand on the title with a clear benefit. Include the primary keyword. End with an implicit CTA.` },
      { n:5, text:`Save. Go to <a href="https://search.google.com/search-console" target="_blank">Google Search Console</a> &rarr; URL Inspection &rarr; <strong>Request Indexing</strong> to speed up re-crawl.` },
      { n:6, text:`Reply <strong>DONE - #${sprint.id}</strong> in the dashboard when complete.` },
    ];
  } else if (type === "CR") {
    steps = [
      { n:1, text:`Open <code>https://www.outdoorbengal.com${url}</code> as a first-time visitor on mobile.` },
      { n:2, text:`In Shopify, rewrite the description: match search intent, address the #1 customer objection, make the value proposition clear in the first 2 sentences.` },
      { n:3, text:`Confirm the <strong>Add to Cart</strong> button is visible without scrolling on mobile.` },
      { n:4, text:`Save. Preview on mobile. Reply <strong>DONE - #${sprint.id}</strong> when done.` },
    ];
  } else if (type === "LINK") {
    steps = [
      { n:1, text:`Open the source post in Shopify editor: <code>${url}</code>` },
      { n:2, text:`Find where the target product is mentioned or relevant and add a hyperlink to the product page.` },
      { n:3, text:`Use descriptive anchor text (e.g. <em>"escape-proof cat harness"</em>, not <em>"click here"</em>).` },
      { n:4, text:`Save. Reply <strong>DONE - #${sprint.id}</strong> when complete.` },
    ];
  } else {
    steps = [
      { n:1, text:`Review the rationale and make the described change on <code>https://www.outdoorbengal.com${url}</code>` },
      { n:2, text:`Save. Reply <strong>DONE - #${sprint.id}</strong> when complete.` },
    ];
  }
  return steps.map(s => `<div class="detail-brief-step"><div class="detail-brief-num">${s.n}</div><div class="detail-brief-text">${s.text}</div></div>`).join("");
}

function openSprintDetail(id) {
  const sprint = findSprintInDetails(id);
  if (!sprint) return;
  const kpi = sprint.kpi || {};
  const effort = sprint.effort || {};
  const evaluation = sprint.evaluation || {};
  const typeLabel = { RNK:"Ranking", CTR:"CTR", CR:"Conversion", LINK:"Internal Link" }[sprint.sprint_type] || sprint.sprint_type;
  document.getElementById("modal-body").innerHTML = `
    <div class="detail-pad">
      <span class="detail-type-badge">${escapeHtml(typeLabel)} &middot; SPRINT #${escapeHtml(sprint.id)}</span>
      <div class="detail-title">${escapeHtml(sprint.title)}</div>
      <div class="detail-url"><a href="https://www.outdoorbengal.com${escapeHtml(sprint.url||"")}" target="_blank" style="color:var(--muted2);font-size:12px;">${escapeHtml(sprint.url||"")}</a></div>

      <div class="detail-section">
        <div class="detail-section-title">Why this sprint</div>
        <div class="detail-rationale">${escapeHtml(sprint.rationale||"No rationale provided.")}</div>
      </div>

      <div class="detail-section">
        <div class="detail-section-title">KPI &amp; target</div>
        <div class="detail-kpi">
          <div class="detail-kpi-item"><div class="k">Current</div><div class="v">${escapeHtml(kpi.current_display||String(kpi.current_value??"—"))}</div></div>
          <div class="detail-kpi-item"><div class="k">Target</div><div class="v" style="color:var(--teal)">${escapeHtml(kpi.target_display||String(kpi.target_value??"—"))}</div></div>
        </div>
      </div>

      <div class="detail-brief">
        <div class="detail-brief-title">Execution brief — for Bart or VA</div>
        ${buildBriefSteps(sprint)}
      </div>

      <div class="detail-section">
        <div class="detail-section-title">Effort &amp; tracking</div>
        <div class="detail-effort-row"><span class="lbl">Effort</span><span class="val">${escapeHtml(effort.level||"—")} &mdash; ${escapeHtml(effort.description||"—")}</span></div>
        <div class="detail-effort-row"><span class="lbl">Estimated time</span><span class="val">${effort.estimated_minutes||"?"} min</span></div>
        <div class="detail-effort-row"><span class="lbl">Tracks for</span><span class="val">${evaluation.window_days||"?"} days after DONE</span></div>
        <div class="detail-effort-row"><span class="lbl">Success threshold</span><span class="val">+${evaluation.success_threshold_pct||10}% over baseline</span></div>
      </div>

      <div class="detail-actions">
        <button class="rbtn r-done" data-modal-action="done" data-sprint-id="${escapeHtml(sprint.id)}">&#10003; DONE &mdash; Start experiment</button>
        <button class="rbtn r-skip" data-modal-action="skip" data-sprint-id="${escapeHtml(sprint.id)}">SKIP</button>
      </div>
    </div>
  `;
  document.querySelectorAll("[data-modal-action]").forEach((btn) => {
    btn.addEventListener("click", () => { closeModal(); handleAction(btn.dataset.modalAction, btn.dataset.sprintId); });
  });
  openModal();
}

function openExperimentDetail(id) {
  const exp = findExperimentInDetails(id);
  if (!exp) return;

  const kpi = exp.kpi || {};
  const tl = exp.timeline || {};

  document.getElementById("modal-body").innerHTML = `
    <div class="detail-pad">
      <span class="detail-type-badge">${escapeHtml(exp.sprint_type)} · EXPERIMENT #${escapeHtml(exp.id)} · ${escapeHtml(exp.status)}</span>
      <div class="detail-title">${escapeHtml(exp.name)}</div>
      <div class="detail-url">${escapeHtml(exp.url || "")}</div>

      <div class="detail-section">
        <div class="detail-section-title">KPI</div>
        <div style="font-size:13px;margin-bottom:10px;">${escapeHtml(kpi.display_label || "")}</div>
        <div class="detail-kpi">
          <div class="detail-kpi-item"><div class="k">Baseline</div><div class="v">${escapeHtml(kpi.baseline_display || kpi.baseline_value || "—")}</div></div>
          <div class="detail-kpi-item"><div class="k">${exp.status === "RUNNING" ? "Target" : "Final"}</div><div class="v" style="color:var(--teal)">${escapeHtml(exp.status === "RUNNING" ? (kpi.target_display || kpi.target_value || "—") : (kpi.final_display || kpi.final_value || "—"))}</div></div>
        </div>
        ${kpi.delta_display ? `<div style="margin-top:10px;font-size:14px;font-weight:600;color:${kpi.delta_pct >= 0 ? "var(--teal)" : "var(--red)"}">Δ ${escapeHtml(kpi.delta_display)}</div>` : ""}
      </div>

      <div class="detail-section">
        <div class="detail-section-title">Rationale</div>
        <div class="detail-rationale">${escapeHtml(exp.rationale || "—")}</div>
      </div>

      <div class="detail-section">
        <div class="detail-section-title">Timeline</div>
        <div class="detail-effort-row"><span class="lbl">Started</span><span class="val">${escapeHtml(tl.started || "—")}</span></div>
        <div class="detail-effort-row"><span class="lbl">${exp.status === "RUNNING" ? "Evaluates on" : "Evaluated on"}</span><span class="val">${escapeHtml(tl.evaluation_date || tl.evaluated || "—")}</span></div>
        <div class="detail-effort-row"><span class="lbl">Window</span><span class="val">${tl.window_days || "?"} days</span></div>
      </div>

      ${exp.outcome_note ? `<div class="detail-section"><div class="detail-section-title">Outcome</div><div class="detail-rationale">${escapeHtml(exp.outcome_note)}</div></div>` : ""}
    </div>
  `;

  openModal();
}

function openModal() {
  document.getElementById("modal").classList.remove("hidden");
  document.getElementById("modal").setAttribute("aria-hidden", "false");
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
  document.getElementById("modal").setAttribute("aria-hidden", "true");
}

document.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", closeModal));

// ─────────────────────────────────────────────
// Run log viewer
// ─────────────────────────────────────────────
let logFilter = "all";

function openLogModal() {
  renderLogRows();
  document.getElementById("logmodal").classList.remove("hidden");
}

function closeLogModal() {
  document.getElementById("logmodal").classList.add("hidden");
}

document.querySelectorAll("[data-close-log]").forEach((el) => el.addEventListener("click", closeLogModal));

document.querySelectorAll(".log-filter").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".log-filter").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    logFilter = btn.dataset.filter;
    renderLogRows();
  });
});

function renderLogRows() {
  const all = currentData?.log || [];
  const filters = logFilter.split(",");
  const filtered = logFilter === "all" ? all : all.filter((r) => filters.includes(r.type) || filters.includes(r.status));

  const html = filtered.map((r) => {
    const rowCls = r.status === "warning" ? "warning" : r.status === "error" ? "error" : "";
    const ts = r.ts ? r.ts.replace("T", " ").replace("Z", "") : "";
    return `<div class="log-row ${rowCls}">
      <div class="log-ts">${escapeHtml(ts)}</div>
      <div class="log-type">${escapeHtml(r.type)}</div>
      <div class="log-summary">${escapeHtml(r.summary || "")}</div>
    </div>`;
  }).join("") || '<div class="empty">No log entries match this filter</div>';

  document.getElementById("log-rows").innerHTML = html;
}

// ─────────────────────────────────────────────
// Action confirmation + dispatch
// ─────────────────────────────────────────────
function confirm({ title, message, withInput, inputPlaceholder, onConfirm }) {
  const dlg = document.getElementById("confirm");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-msg").textContent = message;
  const wrap = document.getElementById("confirm-input-wrap");
  const input = document.getElementById("confirm-input");
  if (withInput) {
    wrap.classList.remove("hidden");
    input.value = "";
    input.placeholder = inputPlaceholder || "";
  } else {
    wrap.classList.add("hidden");
  }
  dlg.classList.remove("hidden");

  const cleanup = () => {
    dlg.classList.add("hidden");
    document.getElementById("confirm-ok").removeEventListener("click", ok);
    document.getElementById("confirm-cancel").removeEventListener("click", cancel);
  };
  const ok = () => { const v = input.value; cleanup(); onConfirm(v); };
  const cancel = () => cleanup();
  document.getElementById("confirm-ok").addEventListener("click", ok);
  document.getElementById("confirm-cancel").addEventListener("click", cancel);
}

async function handleAction(action, id) {
  if (!Auth.current) {
    showToast("Please sign in first", "error");
    return;
  }
  if (!Auth.isAuthorized()) {
    showToast("You're not authorized to trigger actions on this repo", "error");
    return;
  }

  if (action === "done") {
    confirm({
      title: `Mark sprint #${id} as DONE?`,
      message: "This starts the experiment and begins KPI tracking. The baseline is recorded from the current metric, and evaluation runs automatically after the sprint window.",
      withInput: false,
      onConfirm: () => dispatchAction("done", id),
    });
  } else if (action === "skip") {
    confirm({
      title: `Skip sprint #${id}?`,
      message: "The sprint will be removed from the queue and logged. The next queued sprint will be promoted.",
      withInput: true,
      inputPlaceholder: "Optional reason",
      onConfirm: (reason) => dispatchAction("skip", id, { reason }),
    });
  }
}

async function dispatchAction(action, sprintId, extra = {}) {
  try {
    showToast("Dispatching…");
    await Auth.dispatch("sprint-action", { action, sprint_id: sprintId, ...extra });
    showToast(`Sprint #${sprintId} ${action.toUpperCase()} dispatched. Dashboard will update in ~30s.`, "success");
    setTimeout(() => location.reload(), 6000);
  } catch (err) {
    console.error(err);
    showToast("Dispatch failed — see console", "error");
  }
}

// ─────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────
(async function boot() {
  // Handle OAuth callback if we're returning from GitHub
  const returnedFromOAuth = await Auth.handleCallback();

  const hasAuth = await Auth.validate();
  if (!hasAuth) {
    document.getElementById("loading").classList.add("hidden");
    document.getElementById("gate").classList.remove("hidden");
    document.getElementById("login-btn").addEventListener("click", () => Auth.beginLogin());
    return;
  }

  try {
    const data = await loadData();
    render(data);
  } catch (err) {
    console.error(err);
    document.getElementById("loading").innerHTML = `<div style="color:var(--red);text-align:center"><div>Failed to load dashboard data</div><div style="font-size:11px;margin-top:8px;color:var(--muted)">${escapeHtml(err.message)}</div></div>`;
  }
})();
