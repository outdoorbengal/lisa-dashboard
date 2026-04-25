#!/usr/bin/env python3
"""
build_data.py — deterministic generator for public/data.json

Reads source-of-truth YAML files and produces the data.json that the
dashboard consumes. No LLM touches this file; it is purely mechanical
transformation. If the YAML is valid, the JSON is valid.

Run locally:
    python scripts/build_data.py

Run in CI:
    See .github/workflows/build-dashboard.yml
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE = REPO_ROOT / "sprints" / "queue.yml"
ACTIVE = REPO_ROOT / "sprints" / "active.yml"
ARCHIVE = REPO_ROOT / "sprints" / "archive.yml"
SCANS = REPO_ROOT / "scans" / "state.yml"
LOGS = REPO_ROOT / "logs" / "runs.jsonl"
OUT = REPO_ROOT / "public" / "data.json"

# Bar-width normalization scales, per sprint type. The dashboard's before/after
# bars are visual only — these constants control the "5% of scale = full bar"
# kind of mapping so small metrics don't render as zero-width slivers.
BAR_SCALE = {
    "RNK": 100.0,   # position / CTR / rank metrics expressed as percent
    "CTR": 5.0,     # CTR values are typically 0-5%
    "CR":  5.0,     # conversion rates typically 0-5%
    "LINK": 50.0,   # referral sessions — scale against target
    "COUNT": None,  # absolute counts — scale against target dynamically
}


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def bar_pct(value: float | None, sprint_type: str, target: float | None = None, unit: str = "percent") -> float | None:
    """Normalize a raw metric value to a 0-100 bar width."""
    if value is None:
        return None
    # Absolute count metrics: scale against 125% of target so the bar never overflows
    if unit == "count" or sprint_type == "COUNT":
        scale = max(target * 1.25, 10.0) if target else max(value * 1.5, 10.0)
        return round(min((value / scale) * 100, 100.0), 1)
    if sprint_type == "LINK" and target:
        scale = max(target * 1.25, 10.0)
    else:
        scale = BAR_SCALE.get(sprint_type, 100.0)
    return round(min((value / scale) * 100, 100.0), 1)


def days_since(start: str) -> int:
    # Use yesterday as end — never include today's partial data in rate calculations
    yesterday = date.today() - timedelta(days=1)
    return max((yesterday - date.fromisoformat(start)).days, 0)


def mon_dd(iso_date: str) -> str:
    return datetime.fromisoformat(iso_date).strftime("%b %d")


def format_range(start_iso: str | None, end_iso: str | None) -> str:
    """Format a date range as 'Mar 15 – Apr 14' for panel headers."""
    if not start_iso or not end_iso:
        return ""
    try:
        s = date.fromisoformat(start_iso)
        e = date.fromisoformat(end_iso)
    except (ValueError, TypeError):
        return ""
    # Use %-d on POSIX to strip leading zero; fall back to lstrip for Windows.
    try:
        return f"{s.strftime('%b %-d')} – {e.strftime('%b %-d')}"
    except ValueError:
        return f"{s.strftime('%b %d').lstrip('0')} – {e.strftime('%b %d').lstrip('0')}"


def build_system_block(scans: dict, active: dict, queue: dict) -> dict:
    cw = scans.get("current_week", {})
    daily_counts = cw.get("daily_counts", [0] * 7)
    daily_target = cw.get("daily_target", 40)

    # Bar heights as % of daily target, capped at 100
    scan_bars = [min(round(c / daily_target * 100), 100) for c in daily_counts]

    pages_scanned = sum(daily_counts)
    days_complete = sum(1 for c in daily_counts if c > 0)

    sources = cw.get("sources", [])
    sources_connected = sum(1 for s in sources if s.get("connected"))

    active_experiments = active.get("experiments", [])
    active_tests = sum(1 for e in active_experiments if e.get("status") == "RUNNING")

    return {
        "status": "OK",
        "pages_scanned": pages_scanned,
        "pages_total": cw.get("pages_total", 0),
        "sources_connected": sources_connected,
        "sources_total": len(sources),
        "opportunities": cw.get("opportunities_discovered", 0),
        "active_tests": active_tests,
        "days_complete": days_complete,
        "scan_bars": scan_bars,
    }


def build_donuts(archive: dict) -> dict:
    """Count win/total by sprint_type from archived experiments."""
    completed = archive.get("experiments", [])

    categories = ["RNK", "CTR", "CR", "LINK"]
    counts = {cat: {"win": 0, "total": 0} for cat in categories}

    for exp in completed:
        st = exp.get("sprint_type")
        status = exp.get("status")
        if st not in counts:
            continue
        counts[st]["total"] += 1
        if status == "WINNER":
            counts[st]["win"] += 1

    total_win = sum(c["win"] for c in counts.values())
    total_total = sum(c["total"] for c in counts.values())

    return {
        "total": {"win": total_win, "total": total_total},
        "rnk":   counts["RNK"],
        "ctr":   counts["CTR"],
        "cr":    counts["CR"],
        "link":  counts["LINK"],
    }


def _fmt_delta(current: float | None, baseline: float | None, unit: str) -> str | None:
    """Format a delta string — percent change for ratios, absolute for counts."""
    if current is None or baseline is None:
        return None
    if unit == "count":
        diff = current - baseline
        sign = "+" if diff >= 0 else ""
        return f"{sign}{int(round(diff))}"
    # ratios: relative % change vs baseline (NOT percentage-point)
    if baseline == 0:
        if current == 0:
            return "+0"
        return "new"
    rel = (current - baseline) / baseline * 100
    sign = "+" if rel >= 0 else ""
    return f"{sign}{round(rel)}%"


def _fmt_run_rate_delta(
    current_total: float | None,
    current_days: int | None,
    baseline_total: float | None,
    baseline_days: int | None,
) -> tuple[str | None, dict]:
    """
    For absolute-count metrics (impressions, clicks, sessions), comparing a
    completed 30-day baseline window against a partial current window is
    apples-to-oranges. This compares *per-day run rates* instead and returns
    the relative change plus a small dict of derived rates for the UI note.

    Returns (delta_str or None, {current_rate, baseline_rate, current_days, baseline_days}).
    """
    info = {
        "current_rate": None,
        "baseline_rate": None,
        "current_days": current_days,
        "baseline_days": baseline_days,
    }
    if (
        current_total is None or baseline_total is None
        or not current_days or not baseline_days
        or current_days <= 0 or baseline_days <= 0
    ):
        return None, info

    current_rate = current_total / current_days
    baseline_rate = baseline_total / baseline_days
    info["current_rate"] = current_rate
    info["baseline_rate"] = baseline_rate

    if baseline_rate == 0:
        if current_rate == 0:
            return "+0%", info
        return "new", info

    rel = (current_rate - baseline_rate) / baseline_rate * 100
    sign = "+" if rel >= 0 else ""
    return f"{sign}{round(rel)}%", info


def _fmt_int(n: float) -> str:
    return f"{int(round(n)):,}" if abs(n) >= 10 else f"{n:.1f}"


def _metric_label(kpi: dict) -> str:
    """Extract a short, human metric label from the KPI block.

    KPI.display_label is typically structured like "Impressions · /path/here"
    or "Conversion rate · 4772 sessions". Take the text before the first
    separator. Fall back to a title-cased version of kpi.metric.
    """
    dl = (kpi.get("display_label") or "").strip()
    for sep in ("·", "—", "-", "|"):
        if sep in dl:
            head = dl.split(sep, 1)[0].strip()
            if head:
                return head
    if dl:
        return dl
    m = (kpi.get("metric") or "").replace("_", " ").strip()
    return m.title() if m else "Value"


def _fmt_window(days: int) -> str:
    """Render a window length ('1 day', '30 days')."""
    if not days:
        return "—"
    return f"{days} day" if days == 1 else f"{days} days"


# ── Revenue model ────────────────────────────────────────────────────────────
# Hybrid model: the agent writes actual page metrics and its projections
# (CTR, CR, AOV, traffic multiplier) into queue.yml. The dashboard does
# the math here so the numbers stay auditable end-to-end.
#
# Expected shape in queue.yml[].revenue_model:
#   current:
#     impressions_30d: int
#     ctr: float (0..1)              # clickthrough rate as decimal
#     sessions_30d: int              # usually impressions*ctr, kept explicit
#                                    # so it can reflect observed GA4 organic
#     conversion_rate: float (0..1)
#     aov: float                     # average order value in USD
#   projected:                       # include ONLY fields that change
#     ctr: float | null
#     conversion_rate: float | null
#     aov: float | null
#     impressions_multiplier: float | null   # 1.0 = no change, 3.0 = triples
#   confidence: "low" | "medium" | "high"
#
# Haircut: we multiply the computed lift by {low:0.3, medium:0.6, high:1.0}
# so the displayed monthly lift is a conservative, operator-trustworthy
# number. The engine's optimistic raw number is still available in the JSON
# if we ever want to show it.

_HAIRCUTS = {"low": 0.30, "medium": 0.60, "high": 1.00}


def compute_revenue_model(rm: dict | None) -> dict:
    """Given a `revenue_model` block from queue.yml, produce a dict of
    rendered strings + numeric breakdowns for the dashboard.

    Returns a fully-populated dict even when inputs are missing, with
    `available: False` in that case so the UI can show a muted placeholder
    instead of a broken card.
    """
    empty = {
        "available": False,
        "monthly_current_usd": 0.0,
        "monthly_projected_usd": 0.0,
        "monthly_lift_usd": 0.0,
        "monthly_lift_usd_adjusted": 0.0,
        "annual_lift_usd_adjusted": 0.0,
        "confidence": None,
        "haircut_pct": None,
        "breakdown": [],  # ordered list of dicts: {label, before, after, delta_pct}
    }
    if not rm or not isinstance(rm, dict):
        return empty

    cur = rm.get("current") or {}
    prj = rm.get("projected") or {}

    try:
        imp   = float(cur.get("impressions_30d") or 0)
        ctr   = float(cur.get("ctr") or 0)
        sess  = float(cur.get("sessions_30d") or (imp * ctr))
        cr    = float(cur.get("conversion_rate") or 0)
        aov   = float(cur.get("aov") or 0)
    except (TypeError, ValueError):
        return empty

    if imp <= 0 or aov <= 0:
        # No meaningful monetary baseline — can't project a lift.
        return empty

    # Projected values default to current when the field is absent.
    imp_mult_p = float(prj.get("impressions_multiplier") or 1.0)
    ctr_p      = float(prj.get("ctr") if prj.get("ctr") is not None else ctr)
    cr_p       = float(prj.get("conversion_rate") if prj.get("conversion_rate") is not None else cr)
    aov_p      = float(prj.get("aov") if prj.get("aov") is not None else aov)
    imp_p      = imp * imp_mult_p
    sess_p     = imp_p * ctr_p  # rebuild from the top of the funnel

    # Revenue: the classic ecommerce chain.
    current_rev   = sess * cr * aov
    projected_rev = sess_p * cr_p * aov_p
    raw_lift      = projected_rev - current_rev

    confidence = (rm.get("confidence") or "medium").lower()
    haircut    = _HAIRCUTS.get(confidence, _HAIRCUTS["medium"])
    adj_lift   = raw_lift * haircut

    # Per-line breakdown so the UI can show the math (subordinated visually)
    def _pct(a: float, b: float) -> str:
        if a == 0:
            return "+∞" if b > 0 else "0"
        return f"{'+' if b >= a else ''}{round((b - a) / a * 100)}%"

    def _row(label, before, after, fmt):
        return {
            "label": label,
            "before": fmt(before),
            "after": fmt(after),
            "delta_pct": _pct(before, after) if before != after else "",
            "changed": before != after,
        }

    breakdown = [
        _row("Impressions / mo", imp, imp_p, lambda x: f"{int(round(x)):,}"),
        _row("CTR",              ctr, ctr_p, lambda x: f"{x * 100:.2f}%"),
        _row("Sessions / mo",    sess, sess_p, lambda x: f"{int(round(x)):,}"),
        _row("Conversion rate",  cr,  cr_p,  lambda x: f"{x * 100:.2f}%"),
        _row("AOV",              aov, aov_p, lambda x: f"${x:,.0f}"),
    ]

    return {
        "available": True,
        "monthly_current_usd": round(current_rev, 2),
        "monthly_projected_usd": round(projected_rev, 2),
        "monthly_lift_usd": round(raw_lift, 2),
        "monthly_lift_usd_adjusted": round(adj_lift, 2),
        "annual_lift_usd_adjusted": round(adj_lift * 12, 2),
        "confidence": confidence,
        "haircut_pct": int(haircut * 100),
        "breakdown": breakdown,
    }


def fmt_usd_short(amount: float) -> str:
    """Compact USD for headlines: $1,234 · $12.4K · $1.2M."""
    a = abs(amount)
    sign = "-" if amount < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.1f}M"
    if a >= 10_000:
        return f"{sign}${a / 1_000:.1f}K"
    if a >= 1_000:
        return f"{sign}${a:,.0f}"
    return f"{sign}${a:,.0f}"


def _fmt_current_display(value: float | None, unit: str) -> str | None:
    """Format a raw current_reading value for display."""
    if value is None:
        return None
    if unit == "count":
        return f"{int(round(value)):,}"
    # trim trailing zeros, keep up to 2 decimals
    txt = f"{value:.2f}".rstrip("0").rstrip(".")
    return txt + ("%" if unit == "percent" else "")


def build_experiment_rows(active: dict, archive: dict) -> list[dict]:
    """Merge active (RUNNING) and recent archive (COMPLETED) for dashboard display."""
    rows: list[dict] = []

    # Running experiments first
    for exp in active.get("experiments", []):
        if exp.get("status") != "RUNNING":
            continue
        kpi = exp.get("kpi", {})
        tl = exp.get("timeline", {})
        cr = exp.get("current_reading") or {}
        st = exp.get("sprint_type", "RNK")

        days_elapsed = days_since(tl["started"]) if tl.get("started") else 0
        window = tl.get("window_days", 28)
        progress = min(round(days_elapsed / window * 100), 100) if window else 0

        unit = kpi.get("unit", "percent")

        # Current reading written by lisa_dashboard_sync.py into
        # sprints/active.yml[].current_reading. May be null if never measured
        # or if the first scan hasn't run yet.
        current_val = cr.get("current_value")
        current_note = cr.get("note") or ""
        current_measured = cr.get("last_measured")

        # ── Window sizes ─────────────────────────────────────────────
        baseline_window_days = kpi.get("baseline_window_days") or 30
        target_window_days = window or 30          # eval window
        current_window_days = max(days_elapsed, 1)  # guard div/0

        # ── Delta calculation ────────────────────────────────────────
        # For absolute-count metrics the baseline is typically a full 30-day
        # window and the current reading is a partial window (N days
        # elapsed). Comparing raw totals is meaningless — a 30-day total
        # always dwarfs a 1-day partial. Compare per-day run-rates instead.
        #
        # For ratio metrics (%, sessions/week, CTR, CR), totals normalize
        # naturally — use the simple relative delta.
        rate_info: dict = {}
        if unit == "count":
            _, rate_info = _fmt_run_rate_delta(
                current_val, current_window_days,
                kpi.get("baseline_value"), baseline_window_days,
            )
            # Recompute a rate-basis delta from per-day rates so the delta
            # reflects exactly what's displayed on the panels.
            b_rate = rate_info.get("baseline_rate")
            c_rate = rate_info.get("current_rate")
            if b_rate is not None and c_rate is not None and b_rate > 0:
                rel = (c_rate - b_rate) / b_rate * 100
                delta_str = f"{'+' if rel >= 0 else ''}{round(rel)}%"
            elif b_rate == 0 and c_rate and c_rate > 0:
                delta_str = "new"
            else:
                delta_str = None
        else:
            delta_str = _fmt_delta(current_val, kpi.get("baseline_value"), unit)

        # ── Display fields ───────────────────────────────────────────
        # For count metrics: express everything as per-day rates so the
        # three panels are directly comparable. The raw totals move out of
        # the value box into the caption underneath.
        # For ratio metrics: leave display as the YAML-authored strings
        # since they're already normalized.
        if unit == "count":
            metric_label = _metric_label(kpi)  # e.g. "Impressions"
            unit_word = metric_label.lower().rstrip("s") + "s per day"  # "impressions per day"

            b_rate = rate_info.get("baseline_rate")
            c_rate = rate_info.get("current_rate")
            target_rate = (kpi.get("target_value") / target_window_days) \
                if (kpi.get("target_value") is not None and target_window_days) else None

            before_display = f"{_fmt_int(b_rate)}/day" if b_rate is not None else "—"
            current_display = f"{_fmt_int(c_rate)}/day" if c_rate is not None else None
            target_display = f"{_fmt_int(target_rate)}/day" if target_rate is not None else (kpi.get("target_display") or "—")

            before_caption  = f"{unit_word.capitalize()} · last {_fmt_window(baseline_window_days)}"
            target_caption  = f"{unit_word.capitalize()} · goal over {_fmt_window(target_window_days)}"
            current_caption = f"{unit_word.capitalize()} · last {_fmt_window(current_window_days)}"
        else:
            before_display  = kpi.get("baseline_display") or (_fmt_current_display(kpi.get("baseline_value"), unit) or "—")
            target_display  = kpi.get("target_display")   or (_fmt_current_display(kpi.get("target_value"), unit) or "—")
            current_display = _fmt_current_display(current_val, unit)
            before_caption  = "Baseline reading"
            target_caption  = "Goal for this experiment"
            current_caption = current_note or ""

        rows.append({
            "id": exp["id"],
            "name": exp["name"],
            "type": st,
            "status": "RUNNING",
            "unit": unit,
            "kpi_label": kpi.get("display_label", ""),

            # Before
            "before_val": kpi.get("baseline_value"),
            "before_display": before_display,
            "before_caption": before_caption,
            "before_pct": bar_pct(kpi.get("baseline_value"), st, kpi.get("target_value"), unit),
            "before_window_days": baseline_window_days,

            # Target
            "target_val": kpi.get("target_value"),
            "target_display": target_display,
            "target_caption": target_caption,
            "target_pct": bar_pct(kpi.get("target_value"), st, kpi.get("target_value"), unit),
            "target_window_days": target_window_days,

            # Current (primary fields)
            "current_val": current_val,
            "current_display": current_display,
            "current_caption": current_caption,
            "current_note": current_note,
            "current_measured": current_measured,
            "current_pct": bar_pct(current_val, st, kpi.get("target_value"), unit),
            "current_window_days": current_window_days,

            # Run-rate comparison fields for count metrics (null for ratios).
            # Kept for consumers that want the raw numbers.
            "current_per_day": rate_info.get("current_rate"),
            "baseline_per_day": rate_info.get("baseline_rate"),

            # Legacy aliases — kept so consumers that still read after_* don't break
            "after_val": current_val,
            "after_display": current_display,
            "after_pct": bar_pct(current_val, st, kpi.get("target_value"), unit),

            "delta": delta_str,
            "delta_basis": "run_rate" if unit == "count" else "relative",
            "start_date": mon_dd(tl["started"]),
            "end_date": mon_dd(tl["evaluation_date"]),
            "progress_pct": progress,
            "days_elapsed": days_elapsed,
            "note": f"{days_elapsed} days in",
            "week_current": min(days_elapsed // 7 + 1, window // 7) if window else 1,
            "week_total": window // 7 if window else 1,
        })

    # Most recent 2 archived experiments
    archived = sorted(
        archive.get("experiments", []),
        key=lambda e: e.get("timeline", {}).get("evaluated", ""),
        reverse=True,
    )[:2]

    for exp in archived:
        kpi = exp.get("kpi", {})
        tl = exp.get("timeline", {})
        st = exp.get("sprint_type", "RNK")
        unit = kpi.get("unit", "percent")

        # For archived: Current panel carries the final reading, Target panel
        # carries the original target so the row stays comparable to RUNNING.
        final_val = kpi.get("final_value")
        final_disp = kpi.get("final_display") or _fmt_current_display(final_val, unit)

        rows.append({
            "id": exp["id"],
            "name": exp["name"],
            "type": st,
            "status": exp.get("status", "NEUTRAL"),
            "unit": unit,
            "kpi_label": kpi.get("display_label", ""),

            # Before
            "before_val": kpi.get("baseline_value"),
            "before_display": kpi.get("baseline_display"),
            "before_pct": bar_pct(kpi.get("baseline_value"), st, kpi.get("target_value"), unit),

            # Target (what we aimed for — preserved for comparison)
            "target_val": kpi.get("target_value"),
            "target_display": kpi.get("target_display"),
            "target_pct": bar_pct(kpi.get("target_value"), st, kpi.get("target_value"), unit),

            # Current panel = the final reading on archived experiments
            "current_val": final_val,
            "current_display": final_disp,
            "current_note": exp.get("outcome_note", ""),
            "current_measured": tl.get("evaluated"),
            "current_pct": bar_pct(final_val, st, kpi.get("target_value"), unit),

            # Aliased for consumers that still use after_*
            "after_val": final_val,
            "after_display": final_disp,
            "after_pct": bar_pct(final_val, st, kpi.get("target_value"), unit),

            "delta": kpi.get("delta_display"),
            "start_date": mon_dd(tl["started"]),
            "end_date": mon_dd(tl["evaluated"]),
            "progress_pct": 100,
            "days_elapsed": tl.get("window_days", 28),
            "note": exp.get("outcome_note", ""),
            "week_current": tl.get("window_days", 28) // 7,
            "week_total": tl.get("window_days", 28) // 7,
        })

    return rows[:5]


def build_sprint_rows(queue: dict) -> list[dict]:
    """Build the dashboard's sprint rows, ranked by adjusted monthly revenue lift.

    Ranking rules:
      1. Sprints with a usable `revenue_model` first, sorted by
         `monthly_lift_usd_adjusted` DESC (haircut-applied, so ranking is
         conservative).
      2. Sprints without a revenue model (legacy / insufficient data) fall
         to the bottom in `dashboard_impact` order — they still get a slot
         but Albert sees the real opportunities first.

    Secondary sort: lower effort wins at ties (LOW > MEDIUM > HIGH).
    """
    raw = list(queue.get("sprints", []))

    effort_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

    def sort_key(sp: dict):
        model = compute_revenue_model(sp.get("revenue_model"))
        # Primary: revenue lift (DESC, so negate). Secondary: effort (ASC).
        primary = -model["monthly_lift_usd_adjusted"] if model["available"] else 0.0
        secondary = effort_rank.get((sp.get("effort") or {}).get("level", "HIGH"), 2)
        # Fallback: legacy dashboard_impact score keeps pre-migration order
        # stable within the "no model" bucket.
        fallback = -float((sp.get("_source") or {}).get("dashboard_impact") or 0)
        has_model_flag = 0 if model["available"] else 1
        return (has_model_flag, primary, secondary, fallback)

    sprints = sorted(raw, key=sort_key)

    rows: list[dict] = []

    for idx, sp in enumerate(sprints[:10]):
        kpi = sp.get("kpi", {})
        effort = sp.get("effort", {})
        st = sp.get("sprint_type", "RNK")

        current = kpi.get("current_value", 0)
        target = kpi.get("target_value", 0)

        days_waiting = days_since(sp["created"]) if sp.get("created") else 0

        rev = compute_revenue_model(sp.get("revenue_model"))

        # Execution brief (agent writes this for top N; missing for the rest).
        brief = sp.get("execution_brief") or {}

        rows.append({
            "id": str(sp["id"]),  # always a string — tolerant of unquoted YAML ints
            "days_waiting": days_waiting,
            "title": sp["title"],
            "url": sp.get("url", ""),
            "sprint_type": st,
            "impact_label": kpi.get("display_label", ""),
            "current_pct": bar_pct(current, st, target) or 5,
            "current_label": kpi.get("current_display", ""),
            "target_pct": bar_pct(target, st),
            "target_label": kpi.get("target_display", ""),
            "effort_label": f"{effort.get('level', '')} — {effort.get('description', '')}" if effort else None,
            "effort_pct": {"LOW": 6, "MEDIUM": 12, "HIGH": 18}.get(effort.get("level", "LOW"), 10),
            "effort_level": effort.get("level", ""),
            "effort_minutes": effort.get("estimated_minutes"),
            "queue_position": idx + 1,
            "status": "UNACKNOWLEDGED" if idx == 0 else ("NEXT_UP" if idx == 1 else "QUEUED"),

            # Revenue model (primary ranking signal)
            "revenue": rev,
            "revenue_headline": fmt_usd_short(rev["monthly_lift_usd_adjusted"]) + "/mo" if rev["available"] else None,

            # Execution brief presence indicator (the detailed text is in the
            # embedded `details.queue` blob — the frontend looks it up on click)
            "has_exec_brief": bool(brief.get("action") or brief.get("summary")),
        })

    return rows


def build_full_sprint_details(queue: dict, active: dict, archive: dict) -> dict:
    """Embed complete sprint objects so detail views work without extra fetches."""
    return {
        "queue": queue.get("sprints", []),
        "active": active.get("experiments", []),
        "archive": archive.get("experiments", []),
    }


def build_recent_log(limit: int = 50) -> list[dict]:
    if not LOGS.exists():
        return []
    lines = LOGS.read_text().strip().splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return entries[:limit]


def main() -> int:
    queue = load_yaml(QUEUE)
    active = load_yaml(ACTIVE)
    archive = load_yaml(ARCHIVE)
    scans = load_yaml(SCANS)

    week_of_iso = scans.get("current_week", {}).get("week_of", date.today().isoformat())
    week_of_display = mon_dd(week_of_iso) + " " + week_of_iso[:4]

    output = {
        "generated": date.today().isoformat(),
        "week_of": week_of_display,
        "system": build_system_block(scans, active, queue),
        "donuts": build_donuts(archive),
        "experiments": build_experiment_rows(active, archive),
        "sprints": build_sprint_rows(queue),
        "details": build_full_sprint_details(queue, active, archive),
        "log": build_recent_log(limit=50),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2))
    print(f"✓ Wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
