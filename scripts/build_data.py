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
    return (date.today() - date.fromisoformat(start)).days


def mon_dd(iso_date: str) -> str:
    return datetime.fromisoformat(iso_date).strftime("%b %d")


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


def build_experiment_rows(active: dict, archive: dict) -> list[dict]:
    """Merge active (RUNNING) and recent archive (COMPLETED) for dashboard display."""
    rows: list[dict] = []

    # Running experiments first
    for exp in active.get("experiments", []):
        if exp.get("status") != "RUNNING":
            continue
        kpi = exp.get("kpi", {})
        tl = exp.get("timeline", {})
        st = exp.get("sprint_type", "RNK")

        days_elapsed = days_since(tl["started"]) if tl.get("started") else 0
        window = tl.get("window_days", 28)
        progress = min(round(days_elapsed / window * 100), 100) if window else 0

        unit = kpi.get("unit", "percent")
        rows.append({
            "id": exp["id"],
            "name": exp["name"],
            "type": st,
            "status": "RUNNING",
            "unit": unit,
            "kpi_label": kpi.get("display_label", ""),
            "before_val": kpi.get("baseline_value"),
            "before_display": kpi.get("baseline_display"),
            "before_pct": bar_pct(kpi.get("baseline_value"), st, kpi.get("target_value"), unit),
            "after_val": None,
            "after_pct": None,
            "target_val": kpi.get("target_value"),
            "target_display": kpi.get("target_display"),
            "target_pct": bar_pct(kpi.get("target_value"), st, kpi.get("target_value"), unit),
            "delta": None,
            "start_date": mon_dd(tl["started"]),
            "end_date": mon_dd(tl["evaluation_date"]),
            "progress_pct": progress,
            "note": f"{days_elapsed} days in",
            "week_current": min(days_elapsed // 7 + 1, window // 7),
            "week_total": window // 7,
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

        rows.append({
            "id": exp["id"],
            "name": exp["name"],
            "type": st,
            "status": exp.get("status", "NEUTRAL"),
            "kpi_label": kpi.get("display_label", ""),
            "before_val": kpi.get("baseline_value"),
            "before_pct": bar_pct(kpi.get("baseline_value"), st),
            "after_val": kpi.get("final_value"),
            "after_pct": bar_pct(kpi.get("final_value"), st),
            "target_val": None,
            "target_pct": None,
            "delta": kpi.get("delta_display"),
            "start_date": mon_dd(tl["started"]),
            "end_date": mon_dd(tl["evaluated"]),
            "progress_pct": 100,
            "note": exp.get("outcome_note", ""),
            "week_current": tl.get("window_days", 28) // 7,
            "week_total": tl.get("window_days", 28) // 7,
        })

    return rows[:5]


def build_sprint_rows(queue: dict) -> list[dict]:
    """First queued sprint = UNACKNOWLEDGED, second = NEXT_UP."""
    sprints = queue.get("sprints", [])
    rows: list[dict] = []

    for idx, sp in enumerate(sprints[:10]):
        kpi = sp.get("kpi", {})
        effort = sp.get("effort", {})
        st = sp.get("sprint_type", "RNK")

        current = kpi.get("current_value", 0)
        target = kpi.get("target_value", 0)

        days_waiting = days_since(sp["created"]) if sp.get("created") else 0

        rows.append({
            "id": sp["id"],
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
