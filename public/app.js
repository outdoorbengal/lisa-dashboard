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

function tlCls(s) { return { RUNNING: "f-run", WINNER: "f-done-w", LOSER: "f-done-l" }[s] || "f-done-n"; }
function fCls(s)  { return { WINNER: "f-win", LOSER: "f-los" }[s] || "f-neu"; }
function dCls(d)  { if (!d) return "d-f"; return d.startsWith("+") ? "d-p" : d.startsWith("-") ? "d-n" : "d-f"; }

// ── Date math helpers for the 3-panel layout ─────────────────────────
// We derive the three period strings client-side so the panels are
// self-describing. Inputs are already-formatted "Mon DD" strings from
// build_data.py, plus the raw ISO timeline dates when available through
// details.active for RUNNING experiments.
function fmtMonDay(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso + "T00:00:00Z");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  } catch { return ""; }
}
function addDaysISO(iso, days) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
function todayISO() { return new Date().toISOString().slice(0, 10); }

// Build period strings for the 3 panels.
// Before  = 30 days prior to `started`
// Target  = started → evaluation_date (planned window)
// Current = started → today
function panelDateRanges(exp) {
  // Look up raw ISO dates from details.active / details.archive (we embedded full objs)
  const raw = findExperimentRaw(exp.id) || {};
  const tl = raw.timeline || {};
  const started = tl.started || "";
  const evalDate = tl.evaluation_date || tl.evaluated || "";

  const beforeStart = started ? addDaysISO(started, -30) : "";
  const beforeEnd   = started ? addDaysISO(started, -1)  : "";
  const beforeStr   = beforeStart
    ? `${fmtMonDay(beforeStart)} – ${fmtMonDay(beforeEnd)}`
    : exp.start_date || "";

  const targetStr = evalDate
    ? `${fmtMonDay(started)} – ${fmtMonDay(evalDate)}`
    : exp.end_date || "";

  const currentEnd = exp.status === "RUNNING" ? todayISO() : evalDate;
  const currentStr = started
    ? `${fmtMonDay(started)} – ${fmtMonDay(currentEnd)}`
    : "—";

  return { before: beforeStr, target: targetStr, current: currentStr };
}

function findExperimentRaw(id) {
  const details = currentData?.details || {};
  return (details.active || []).find((e) => String(e.id) === String(id))
      || (details.archive || []).find((e) => String(e.id) === String(id));
}

// Sprint-type → CSS class token. One of: rnk, ctr, cr, link.
// Used to color the type pill and the row's left-edge accent.
function typeAccent(t) {
  const x = (t || "").toLowerCase();
  return ["rnk", "ctr", "cr", "link"].includes(x) ? x : "rnk";
}

// Format a per-day rate for inline text. Integer when ≥10, 1 decimal below.
function _fmtRate(n) {
  if (n == null) return "—";
  const abs = Math.abs(n);
  return abs >= 10 ? Math.round(n).toLocaleString() : n.toFixed(1);
}

// Status pill (archived only). RUNNING experiments show no badge —
// the bottom timeline label already carries elapsed days.
function buildStatusBadge(exp) {
  if (exp.status === "RUNNING") return "";
  const cls = { WINNER: "winner", LOSER: "loser", NEUTRAL: "neutral" }[exp.status] || "neutral";
  return `<span class="wk-badge ${cls}">${escapeHtml(exp.status)}</span>`;
}

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

  // ── Experiment rows ────────────────────────────────────────────────
  // 3-panel layout: BEFORE / TARGET / CURRENT.
  // Each panel has label + date-range + big value + supporting note.
  // Elapsed days live inside the WK badge. No TBD.
  const exps = (d.experiments || []).map((exp) => {
    const run = exp.status === "RUNNING";

    // Dates for each panel
    const dates = panelDateRanges(exp);

    // Values + supporting notes for each panel
    // Values for each panel — backend now sets these correctly for both
    // count (per-day rates) and ratio (raw %, etc.) metrics. The frontend
    // just renders whatever came in.
    const beforeVal  = exp.before_display  || fmtVal(exp.before_val,  null, exp.unit);
    const targetVal  = exp.target_display  || fmtVal(exp.target_val,  null, exp.unit);
    const currentVal = exp.current_display
      || (exp.current_val != null ? fmtVal(exp.current_val, null, exp.unit) : null)
      || (exp.after_val   != null ? fmtVal(exp.after_val,   exp.after_display, exp.unit) : null);
    const hasCurrent = currentVal !== null && currentVal !== "—";

    // Captions live under each value. Backend provides them; we fall back
    // to sensible defaults for archived experiments that don't yet emit them.
    const beforeCaption  = exp.before_caption  || "Baseline reading";
    const targetCaption  = exp.target_caption  || (run ? "Goal for this experiment" : "End-of-window reading");
    const currentCaption = exp.current_caption || "";

    // Delta: shown once a meaningful reading exists.
    const delta = exp.delta;
    const deltaHtml = delta && hasCurrent
      ? `<div class="delta-tag ${dCls(delta)}">${escapeHtml(delta)}</div>`
      : "";

    // Status pill (archived only). RUNNING shows no top-right badge —
    // the bottom timeline label carries elapsed days per the UX spec.
    const statusBadge = buildStatusBadge(exp);

    // Color class derived from sprint type for the type-tag pill + row accent
    const typeCls = typeAccent(exp.type);

    const currentMeasured = exp.current_measured
      ? `Last scan · ${exp.current_measured.slice(0, 10)}`
      : (run ? "Awaiting first scan" : "");

    // Bottom timeline: center label carries elapsed days for RUNNING,
    // or the outcome note for archived. Start / End dates bracket it.
    const timelineCenter = run
      ? `<span class="tl-elapsed">${exp.days_elapsed || 0}d elapsed</span>`
      : `<span class="tl-elapsed">${escapeHtml(exp.note || "")}</span>`;

    return `<div class="exp-row row-type-${typeCls}" data-exp-id="${escapeHtml(exp.id)}">
      <div class="exp-head">
        <div class="exp-head-left">
          <div class="exp-type-tag tag-${typeCls}">${escapeHtml(exp.type || "")}${run ? "" : " · " + escapeHtml(exp.status)}</div>
          <div class="exp-name">${escapeHtml(exp.name)}</div>
          <div class="exp-kpi-line">${escapeHtml(exp.kpi_label || "")}</div>
        </div>
        <div class="exp-head-right">
          ${deltaHtml}
          ${statusBadge}
        </div>
      </div>

      <div class="exp-panels">
        <div class="exp-panel panel-before">
          <div class="panel-head">
            <div class="panel-label">Before</div>
            <div class="panel-dates">${escapeHtml(dates.before)}</div>
          </div>
          <div class="panel-value muted">${escapeHtml(beforeVal)}</div>
          <div class="panel-note">${escapeHtml(beforeCaption)}</div>
        </div>

        <div class="exp-panel panel-target">
          <div class="panel-head">
            <div class="panel-label">Target</div>
            <div class="panel-dates">${escapeHtml(dates.target)}</div>
          </div>
          <div class="panel-value accent">${escapeHtml(targetVal)}</div>
          <div class="panel-note">${escapeHtml(targetCaption)}</div>
        </div>

        <div class="exp-panel panel-current">
          <div class="panel-head">
            <div class="panel-label accent">Current</div>
            <div class="panel-dates">${escapeHtml(dates.current)}</div>
          </div>
          <div class="panel-value ${hasCurrent ? "" : "pending"}">${escapeHtml(hasCurrent ? currentVal : "Awaiting scan")}</div>
          <div class="panel-note">${escapeHtml(currentCaption || "—")}</div>
          ${currentMeasured ? `<div class="panel-note-sub">${escapeHtml(currentMeasured)}</div>` : ""}
        </div>
      </div>

      <div class="tl-wrap">
        <div class="tl-track"><div class="tl-fill ${tlCls(exp.status)}" style="width:${exp.progress_pct || 0}%"></div></div>
        <div class="tl-lbls">
          <span>${escapeHtml(exp.start_date)}</span>
          ${timelineCenter}
          <span>${escapeHtml(exp.end_date)}</span>
        </div>
      </div>
    </div>`;
  }).join("") || '<div class="empty">No experiments yet</div>';

  const activeCount = (d.experiments || []).filter((e) => e.status === "RUNNING").length;
  const pendingCount = (d.sprints || []).filter((s) => s.status === "UNACKNOWLEDGED").length;

  const sprints = (d.sprints || []).slice(0, 10).map((sp) => {
    const unack = sp.status === "UNACKNOWLEDGED";
    const next  = sp.status === "NEXT_UP";
    const effortCls = { LOW:"effort-low", MEDIUM:"effort-medium", HIGH:"effort-high" }[sp.effort_level] || "effort-medium";
    const typeCls = typeAccent(sp.sprint_type);
    const badge = unack
      ? `<span class="s-unack">${sp.days_waiting}d waiting</span>`
      : next
        ? `<span class="s-next">NEXT UP</span>`
        : `<span class="s-queued">#${sp.queue_position || ""}</span>`;

    // Revenue headline — primary visual priority per the UX spec.
    // Sprints without a revenue model still render but show a muted
    // "—" placeholder; they've been sorted below modeled sprints already.
    const rev = sp.revenue || {};
    const hasRev = rev.available;
    const revCls = hasRev
      ? (rev.monthly_lift_usd_adjusted >= 100 ? "rev-big" : rev.monthly_lift_usd_adjusted >= 20 ? "rev-mid" : "rev-small")
      : "rev-none";
    const confCls = hasRev ? `conf-${rev.confidence}` : "";
    const revHeadline = hasRev
      ? `<div class="sprint-revenue">
           <div class="rev-amount ${revCls}">${escapeHtml(sp.revenue_headline)}</div>
           <div class="rev-sub"><span class="conf-dot ${confCls}"></span>${escapeHtml(rev.confidence)} confidence · ${rev.haircut_pct}%</div>
         </div>`
      : `<div class="sprint-revenue rev-missing"><div class="rev-amount rev-none">—</div><div class="rev-sub">no model</div></div>`;

    return `<div class="sprint-row row-type-${typeCls}" data-sprint-id="${escapeHtml(sp.id)}">
      <div class="sprint-date-blk">
        <div class="sprint-num-lbl">Sprint</div>
        <div class="sprint-num-big">#${escapeHtml(sp.id)}</div>
        <span class="sprint-type-chip tag-${typeCls}">${escapeHtml(sp.sprint_type || "")}</span>
      </div>
      <div class="sprint-body">
        <div class="sprint-title">${escapeHtml(sp.title)}</div>
        <div class="sprint-url">${escapeHtml(sp.url)}</div>
        <div class="sprint-meta">
          <span class="sprint-kpi-pill">${escapeHtml(sp.impact_label || sp.sprint_type || "")}</span>
          ${sp.effort_level ? `<span class="sprint-effort-pill ${effortCls}">${escapeHtml(sp.effort_level)}</span>` : ""}
          ${sp.effort_minutes ? `<span class="sprint-kpi-pill">~${sp.effort_minutes}min</span>` : ""}
        </div>
      </div>
      ${revHeadline}
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
        <div class="stat-card"><div><div class="stat-val">${sys.pages_scanned}<span> / ${sys.pages_total}</span></div><div class="stat-lbl">Pages scanned</div><div class="stat-note">This week</div></div></div>
        <div class="stat-card"><div><div class="stat-val">${sys.sources_connected}<span> / ${sys.sources_total}</span></div><div class="stat-lbl">Data sources</div><div class="stat-note" style="color:var(--pos)">All connected</div></div></div>
        <div class="stat-card"><div><div class="stat-val">${activeCount}</div><div class="stat-lbl">Active tests</div><div class="stat-note">Running now</div></div></div>
        <div class="stat-card"><div><div class="stat-val" style="color:${pendingCount > 0 ? "var(--attn)" : "var(--text-2)"}">${pendingCount}</div><div class="stat-lbl">Pending sprints</div><div class="stat-note" style="color:${pendingCount > 0 ? "var(--attn)" : "var(--muted)"}">${pendingCount > 0 ? "Needs response" : "All acknowledged"}</div></div></div>
      </div></div>

      <div class="section"><div class="card"><div class="scan-wrap"><div class="scan-inner">
        <div><div class="scan-lbl-title">Weekly scan</div><div class="scan-lbl-sub">Pages per day</div></div>
        <div class="scan-chart"><div class="scan-bars">${scanBars}</div><div class="sbar-lbls">${scanLbls}</div></div>
        <div class="scan-stat"><div class="scan-big">${sys.days_complete || 0}/7</div><div class="scan-sm">days complete</div></div>
      </div></div></div></div>

      <div class="section"><div class="card">
        <div class="card-header">
          <div class="card-header-left"><div><div class="card-title">Experiments</div><div class="card-sub">Click any row for full details</div></div></div>
          <div class="card-badge">${activeCount} running</div>
        </div>
        ${exps}
      </div></div>

      <div class="section"><div class="card">
        <div class="card-header">
          <div class="card-header-left"><div><div class="card-title">Sprints</div><div class="card-sub">Click any sprint for full brief &amp; instructions</div></div></div>
          ${pendingCount > 0 ? `<div class="card-badge" style="background:var(--attn-soft);color:var(--attn);border-color:transparent">${pendingCount} unacknowledged</div>` : ""}
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
  const key = String(id);
  return (details.active || []).find((e) => String(e.id) === key)
      || (details.archive || []).find((e) => String(e.id) === key);
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

  // Resolve the row-level derived data (has revenue breakdown, headline, etc.)
  const row = (currentData?.sprints || []).find((r) => String(r.id) === String(id)) || {};

  const kpi = sprint.kpi || {};
  const effort = sprint.effort || {};
  const evaluation = sprint.evaluation || {};
  const typeLabel = { RNK:"Ranking", CTR:"CTR", CR:"Conversion", LINK:"Internal Link" }[sprint.sprint_type] || sprint.sprint_type;

  const rev = row.revenue || {};
  const hasRev = rev.available;
  const revSection = hasRev ? buildRevenueSection(rev, row.revenue_headline) : buildNoRevenueSection();

  const brief = sprint.execution_brief || {};
  const hasBrief = !!(brief.action || brief.summary || (brief.steps || []).length);
  const briefSection = hasBrief
    ? buildExecutionBrief(brief, sprint.id)
    : buildBriefPlaceholder(sprint);

  document.getElementById("modal-body").innerHTML = `
    <div class="detail-pad">
      <span class="detail-type-badge">${escapeHtml(typeLabel)} &middot; SPRINT #${escapeHtml(sprint.id)}</span>
      <div class="detail-title">${escapeHtml(sprint.title)}</div>
      <div class="detail-url"><a href="https://www.outdoorbengal.com${escapeHtml(sprint.url||"")}" target="_blank">${escapeHtml(sprint.url||"")}</a></div>

      ${revSection}

      <div class="detail-section">
        <div class="detail-section-title">Why this sprint</div>
        <div class="detail-rationale">${escapeHtml(sprint.rationale||"No rationale provided.")}</div>
      </div>

      ${briefSection}

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

// ── Revenue section ──────────────────────────────────────────────────
// Primary visual priority: the adjusted monthly lift. The math table is
// present but sized down and muted so it doesn't compete.
function buildRevenueSection(rev, headline) {
  const sizeCls = rev.monthly_lift_usd_adjusted >= 100 ? "rev-big"
                : rev.monthly_lift_usd_adjusted >= 20  ? "rev-mid"
                : "rev-small";
  const confLabel = { low: "low confidence", medium: "medium confidence", high: "high confidence" }[rev.confidence] || "";

  // Math breakdown rows — only show lines that actually change to reduce noise.
  const rows = (rev.breakdown || []).filter((r) => r.changed);
  // Always show at least one row even if nothing marked "changed" — keeps the
  // math visible for edge cases.
  const rowsToShow = rows.length ? rows : (rev.breakdown || []).slice(0, 1);

  const mathRows = rowsToShow.map((r) => `
    <tr class="math-row ${r.changed ? "changed" : ""}">
      <td class="math-lbl">${escapeHtml(r.label)}</td>
      <td class="math-before">${escapeHtml(r.before)}</td>
      <td class="math-arrow">→</td>
      <td class="math-after">${escapeHtml(r.after)}</td>
      <td class="math-delta ${r.delta_pct.startsWith("+") ? "pos" : r.delta_pct.startsWith("-") ? "neg" : ""}">${escapeHtml(r.delta_pct)}</td>
    </tr>`).join("");

  return `
    <div class="detail-section rev-section">
      <div class="rev-headline-row">
        <div>
          <div class="rev-label">Expected revenue lift</div>
          <div class="rev-amount-big ${sizeCls}">${escapeHtml(headline || "")}</div>
          <div class="rev-sub-detail">
            <span class="conf-dot conf-${rev.confidence}"></span>
            ${escapeHtml(confLabel)} · showing ${rev.haircut_pct}% of raw estimate
          </div>
        </div>
      </div>
      <details class="rev-math">
        <summary>Show the math</summary>
        <div class="rev-math-body">
          <div class="rev-math-baseline">
            Current monthly revenue on this page: <strong>$${Number(rev.monthly_current_usd).toLocaleString(undefined, {maximumFractionDigits: 0})}</strong>
            (projected after change: $${Number(rev.monthly_projected_usd).toLocaleString(undefined, {maximumFractionDigits: 0})}, raw lift $${Number(rev.monthly_lift_usd).toLocaleString(undefined, {maximumFractionDigits: 0})})
          </div>
          <table class="rev-math-table">
            <thead>
              <tr><th>Metric</th><th>Current</th><th></th><th>Projected</th><th>Δ</th></tr>
            </thead>
            <tbody>${mathRows}</tbody>
          </table>
          <div class="rev-math-note">Other signals held constant. Lift shown above is the haircut-adjusted figure.</div>
        </div>
      </details>
    </div>`;
}

function buildNoRevenueSection() {
  return `
    <div class="detail-section rev-section rev-missing-section">
      <div class="rev-label">Expected revenue lift</div>
      <div class="rev-amount-big rev-none">—</div>
      <div class="rev-sub-detail">No revenue model yet · agent will add one on next cycle</div>
    </div>`;
}

// ── Execution brief ──────────────────────────────────────────────────
// When the agent has written a detailed brief (top 5 sprints), render it
// with before/after code blocks and explicit out-of-scope guardrails.
// Otherwise fall back to a single-line prompt telling the operator to
// execute the rationale on the page.
function buildExecutionBrief(brief, sprintId) {
  const steps = (brief.steps || []).map((s, idx) => {
    const hasBefore = s.before !== undefined && s.before !== null && s.before !== "";
    const hasAfter  = s.after !== undefined && s.after !== null && s.after !== "";
    const ba = hasBefore || hasAfter
      ? `<div class="brief-ba">
           ${hasBefore ? `<div class="brief-ba-col"><div class="brief-ba-lbl">BEFORE</div><div class="brief-ba-val before">${escapeHtml(s.before)}</div></div>` : ""}
           ${hasAfter  ? `<div class="brief-ba-col"><div class="brief-ba-lbl">AFTER</div><div class="brief-ba-val after">${escapeHtml(s.after)}</div></div>` : ""}
         </div>`
      : "";
    return `
      <div class="brief-step">
        <div class="brief-step-num">${idx + 1}</div>
        <div class="brief-step-body">
          <div class="brief-step-title">${escapeHtml(s.title || "Step " + (idx + 1))}</div>
          ${s.location ? `<div class="brief-step-location">${escapeHtml(s.location)}</div>` : ""}
          ${ba}
          ${s.note ? `<div class="brief-step-note">${escapeHtml(s.note)}</div>` : ""}
        </div>
      </div>`;
  }).join("");

  const outOfScope = (brief.out_of_scope || []).length
    ? `<div class="brief-guardrails">
         <div class="brief-guardrails-title">Out of scope for this sprint</div>
         <ul>${brief.out_of_scope.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>
       </div>`
    : "";

  const dod = brief.definition_of_done
    ? `<div class="brief-dod">
         <div class="brief-dod-lbl">Definition of done</div>
         <div class="brief-dod-text">${escapeHtml(brief.definition_of_done)}</div>
       </div>`
    : "";

  return `
    <div class="detail-brief-v2">
      <div class="detail-brief-title">Execution brief</div>
      ${brief.action ? `<div class="brief-action">${escapeHtml(brief.action)}</div>` : ""}
      ${brief.summary ? `<div class="brief-summary">${escapeHtml(brief.summary)}</div>` : ""}
      <div class="brief-steps">${steps}</div>
      ${outOfScope}
      ${dod}
    </div>`;
}

function buildBriefPlaceholder(sprint) {
  return `
    <div class="detail-brief-v2 brief-placeholder">
      <div class="detail-brief-title">Execution brief</div>
      <div class="brief-placeholder-msg">
        Detailed brief not generated for this sprint (briefs are prepared for the top 5 by expected revenue).
        Execute the change described in "Why this sprint" above, then reply
        <strong>DONE - #${escapeHtml(sprint.id)}</strong>.
      </div>
    </div>`;
}

function openExperimentDetail(id) {
  const exp = findExperimentInDetails(id);
  if (!exp) return;

  const kpi = exp.kpi || {};
  const tl = exp.timeline || {};
  const cr = exp.current_reading || {};
  const unit = kpi.unit || "percent";

  // Also fetch the row-level derived object (has delta, pre-formatted notes)
  const row = (currentData?.experiments || []).find((r) => String(r.id) === String(id)) || {};

  const beforeStart = tl.started ? addDaysISO(tl.started, -30) : "";
  const beforeEnd   = tl.started ? addDaysISO(tl.started, -1)  : "";
  const evalDate    = tl.evaluation_date || tl.evaluated || "";
  const isRunning   = exp.status === "RUNNING";
  const currentEnd  = isRunning ? todayISO() : evalDate;

  const currentValRaw = cr.current_value ?? null;
  const hasCurrent = currentValRaw !== null && currentValRaw !== undefined;

  // Prefer the row-level derived fields (already per-day for count metrics,
  // with matching captions). Fall back to the raw YAML only if the row row
  // is missing something.
  const beforeVal  = row.before_display
    || kpi.baseline_display
    || (kpi.baseline_value != null ? String(kpi.baseline_value) : "—");
  const targetVal = isRunning
    ? (row.target_display || kpi.target_display || (kpi.target_value != null ? String(kpi.target_value) : "—"))
    : (kpi.final_display  || (kpi.final_value  != null ? String(kpi.final_value)  : "—"));
  const currentDisplay = row.current_display
    || (hasCurrent
        ? (unit === "count" ? Math.round(currentValRaw).toLocaleString() : currentValRaw + (unit === "percent" ? "%" : ""))
        : null);

  const beforeCap  = row.before_caption  || "Baseline reading";
  const targetCap  = row.target_caption  || (isRunning ? "Goal for this experiment" : "End-of-window reading");
  const currentCap = row.current_caption || "";

  const currentMeasured = cr.last_measured ? cr.last_measured.slice(0, 10) : "";

  const deltaHtml = row.delta && hasCurrent
    ? `<div class="delta-tag ${dCls(row.delta)}" style="margin-left:8px">${escapeHtml(row.delta)}</div>`
    : "";

  document.getElementById("modal-body").innerHTML = `
    <div class="detail-pad">
      <span class="detail-type-badge">${escapeHtml(exp.sprint_type || "")} · EXPERIMENT #${escapeHtml(exp.id)} · ${escapeHtml(exp.status || "")}</span>
      <div class="detail-title">${escapeHtml(exp.name)}</div>
      <div class="detail-url">${escapeHtml(exp.url || "")}</div>

      <div class="detail-section">
        <div class="detail-section-title" style="display:flex;align-items:center;gap:6px">
          KPI · ${escapeHtml(kpi.display_label || "")} ${deltaHtml}
        </div>
        <div class="detail-panels">
          <div class="exp-panel panel-before">
            <div class="panel-head">
              <div class="panel-label">Before</div>
              <div class="panel-dates">${fmtMonDay(beforeStart)} – ${fmtMonDay(beforeEnd)}</div>
            </div>
            <div class="panel-value muted">${escapeHtml(beforeVal)}</div>
            <div class="panel-note">${escapeHtml(beforeCap)}</div>
          </div>
          <div class="exp-panel panel-target">
            <div class="panel-head">
              <div class="panel-label">${isRunning ? "Target" : "Final"}</div>
              <div class="panel-dates">${fmtMonDay(tl.started)} – ${fmtMonDay(evalDate)}</div>
            </div>
            <div class="panel-value ${isRunning ? "accent" : ""}">${escapeHtml(targetVal)}</div>
            <div class="panel-note">${escapeHtml(targetCap)}</div>
          </div>
          <div class="exp-panel panel-current">
            <div class="panel-head">
              <div class="panel-label accent">Current</div>
              <div class="panel-dates">${fmtMonDay(tl.started)} – ${fmtMonDay(currentEnd)}</div>
            </div>
            <div class="panel-value ${hasCurrent ? "" : "pending"}">${escapeHtml(hasCurrent ? currentDisplay : "Awaiting scan")}</div>
            <div class="panel-note">${escapeHtml(currentCap || "—")}</div>
            ${currentMeasured ? `<div class="panel-note-sub">Last scan · ${escapeHtml(currentMeasured)}</div>` : ""}
          </div>
        </div>
      </div>

      <div class="detail-section">
        <div class="detail-section-title">Rationale</div>
        <div class="detail-rationale">${escapeHtml(exp.rationale || "—")}</div>
      </div>

      <div class="detail-section">
        <div class="detail-section-title">Timeline</div>
        <div class="detail-effort-row"><span class="lbl">Started</span><span class="val">${escapeHtml(tl.started || "—")}</span></div>
        <div class="detail-effort-row"><span class="lbl">${isRunning ? "Evaluates on" : "Evaluated on"}</span><span class="val">${escapeHtml(evalDate || "—")}</span></div>
        <div class="detail-effort-row"><span class="lbl">Window</span><span class="val">${tl.window_days || "?"} days</span></div>
        ${isRunning ? `<div class="detail-effort-row"><span class="lbl">Progress</span><span class="val">${row.days_elapsed || 0} / ${tl.window_days || "?"} days</span></div>` : ""}
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
    document.getElementById("loading").innerHTML = `<div style="color:var(--neg);text-align:center"><div>Failed to load dashboard data</div><div style="font-size:11px;margin-top:8px;color:var(--muted)">${escapeHtml(err.message)}</div></div>`;
  }
})();
