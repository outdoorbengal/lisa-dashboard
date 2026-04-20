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
      ? `<div class="ba-row"><div class="ba-tag">Target</div><div class="ba-track"><div class="ba-fill f-target" style="width:${exp.target_pct || 0}%"></div></div><div class="ba-val">${exp.target_val != null ? exp.target_val + "%" : "—"}</div></div>`
      : `<div class="ba-row"><div class="ba-tag" style="color:${exp.status === "WINNER" ? "var(--teal)" : "var(--text2)"}">After</div><div class="ba-track"><div class="ba-fill ${fCls(exp.status)}" style="width:${exp.after_pct || 0}%"></div></div><div class="ba-val" style="color:${exp.status === "WINNER" ? "var(--teal)" : "var(--text2)"}">${ha ? exp.after_val + "%" : "—"}</div></div>`;
    const deltaHtml = exp.delta ? `<div class="delta-tag ${dCls(exp.delta)}">${exp.delta}</div>` : run ? `<div class="delta-tag d-f">TBD</div>` : "";
    return `<div class="exp-row" data-exp-id="${escapeHtml(exp.id)}">
      <div class="exp-date-blk"><div class="exp-date-mon">${sd.mon}</div><div class="exp-date-num">${sd.day}</div></div>
      <div>
        <div class="exp-name">${escapeHtml(exp.name)}</div>
        <div class="exp-url">${escapeHtml(exp.kpi_label)}${run ? " · baseline week" : ""}</div>
        <div class="ba-row-wrap">
          <div class="ba-row"><div class="ba-tag">Before</div><div class="ba-track"><div class="ba-fill f-before" style="width:${exp.before_pct}%"></div></div><div class="ba-val">${exp.before_val}%</div></div>
          ${afterRow}
        </div>
        <div class="tl-wrap">
          <div class="tl-track"><div class="tl-fill ${tlCls(exp.status)}" style="width:${exp.progress_pct}%"></div></div>
          <div class="tl-lbls"><span>${exp.start_date}</span><span style="color:${exp.status === "WINNER" ? "var(--teal)" : "var(--muted)"};font-weight:500">${escapeHtml(exp.note || "")}</span><span>${exp.end_date}</span></div>
        </div>
      </div>
      <div class="exp-right"><span class="e-badge ${bCls(exp.status)}">${bLbl(exp)}</span>${deltaHtml}</div>
    </div>`;
  }).join("") || '<div class="empty">No experiments yet</div>';

  const activeCount = (d.experiments || []).filter((e) => e.status === "RUNNING").length;
  const winRate = pct(d.donuts.total.win, d.donuts.total.total);
  const pendingCount = (d.sprints || []).filter((s) => s.status === "UNACKNOWLEDGED").length;

  const sprints = (d.sprints || []).map((sp) => {
    const next = sp.status === "NEXT_UP";
    const eff = sp.effort_label ? `<div class="s-effort-wrap"><div class="s-effort-fill" style="width:${sp.effort_pct}%"></div><div class="s-effort-lbl">${escapeHtml(sp.effort_label)}</div></div>` : "";
    return `<div class="sprint-row">
      <div class="sprint-date-blk"><div class="sprint-num-lbl">Sprint</div><div class="sprint-num-big">#${escapeHtml(sp.id)}</div></div>
      <div>
        <div class="sprint-header-clickable" data-sprint-id="${escapeHtml(sp.id)}">
          <div class="sprint-title">${escapeHtml(sp.title)}</div>
          <div class="sprint-url">${escapeHtml(sp.url)}</div>
          <div class="s-impact-lbl">${escapeHtml(sp.impact_label)}</div>
          <div class="s-bar-wrap">
            <div class="s-bar-current" style="width:${sp.current_pct}%"></div>
            <div class="s-bar-val-l">${escapeHtml(sp.current_label)}</div>
            ${sp.target_pct ? `<div class="s-bar-target" style="width:${sp.target_pct}%"><span class="s-bar-val-r">${escapeHtml(sp.target_label)}</span></div>` : ""}
          </div>
          ${eff}
        </div>
        ${!next ? `<div class="reply-btns">
          <button class="rbtn r-done" data-action="done" data-sprint-id="${escapeHtml(sp.id)}">DONE</button>
          <button class="rbtn r-skip" data-action="skip" data-sprint-id="${escapeHtml(sp.id)}">SKIP</button>
          <button class="rbtn r-mod" data-sprint-id="${escapeHtml(sp.id)}">REVIEW</button>
        </div>` : ""}
      </div>
      <div class="sprint-right">${next ? '<span class="s-next">NEXT UP</span>' : `<span class="s-unack">${sp.days_waiting}d waiting</span>`}</div>
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
          <div class="card-header-left"><div class="sec-icon" style="background:var(--orange-lt)">⚡</div><div><div class="card-title">Sprints</div><div class="card-sub">Click header to review · then DONE / SKIP / REVIEW</div></div></div>
          ${pendingCount > 0 ? `<div class="card-badge" style="background:var(--orange-lt);color:var(--orange);border:1px solid #fca5a5">${pendingCount} unacknowledged</div>` : ""}
        </div>
        ${sprints}
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

  // Sprint detail (click on sprint header)
  document.querySelectorAll("[data-sprint-id]").forEach((el) => {
    if (el.classList.contains("sprint-header-clickable") || el.classList.contains("r-mod")) {
      el.addEventListener("click", (e) => {
        if (e.currentTarget.dataset.action) return;
        openSprintDetail(e.currentTarget.dataset.sprintId);
      });
    }
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

function openSprintDetail(id) {
  const sprint = findSprintInDetails(id);
  if (!sprint) return;

  const kpi = sprint.kpi || {};
  const effort = sprint.effort || {};
  const evaluation = sprint.evaluation || {};

  document.getElementById("modal-body").innerHTML = `
    <div class="detail-pad">
      <span class="detail-type-badge">${escapeHtml(sprint.sprint_type)} · SPRINT #${escapeHtml(sprint.id)}</span>
      <div class="detail-title">${escapeHtml(sprint.title)}</div>
      <div class="detail-url">${escapeHtml(sprint.url || "")}</div>

      <div class="detail-section">
        <div class="detail-section-title">KPI we're optimizing</div>
        <div style="font-size:13px;margin-bottom:10px;">${escapeHtml(kpi.display_label || "")}</div>
        <div class="detail-kpi">
          <div class="detail-kpi-item"><div class="k">Current</div><div class="v">${escapeHtml(kpi.current_display || kpi.current_value || "—")}</div></div>
          <div class="detail-kpi-item"><div class="k">Target</div><div class="v" style="color:var(--teal)">${escapeHtml(kpi.target_display || kpi.target_value || "—")}</div></div>
        </div>
      </div>

      <div class="detail-section">
        <div class="detail-section-title">Rationale</div>
        <div class="detail-rationale">${escapeHtml(sprint.rationale || "No rationale provided.")}</div>
      </div>

      <div class="detail-section">
        <div class="detail-section-title">Effort & timing</div>
        <div class="detail-effort-row"><span class="lbl">Effort level</span><span class="val">${escapeHtml(effort.level || "—")}</span></div>
        <div class="detail-effort-row"><span class="lbl">Description</span><span class="val">${escapeHtml(effort.description || "—")}</span></div>
        <div class="detail-effort-row"><span class="lbl">Estimated time</span><span class="val">${effort.estimated_minutes || "?"} min</span></div>
        <div class="detail-effort-row"><span class="lbl">Evaluation window</span><span class="val">${evaluation.window_days || "?"} days after DONE</span></div>
        <div class="detail-effort-row"><span class="lbl">Success threshold</span><span class="val">+${evaluation.success_threshold_pct || 10}% over baseline</span></div>
      </div>

      <div class="detail-actions">
        <button class="rbtn r-done" data-modal-action="done" data-sprint-id="${escapeHtml(sprint.id)}">✓ DONE — Start tracking</button>
        <button class="rbtn r-skip" data-modal-action="skip" data-sprint-id="${escapeHtml(sprint.id)}">SKIP</button>
      </div>
    </div>
  `;

  document.querySelectorAll("[data-modal-action]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      closeModal();
      handleAction(btn.dataset.modalAction, btn.dataset.sprintId);
    });
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
