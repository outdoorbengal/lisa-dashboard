#!/usr/bin/env python3
"""
evaluate_experiment.py — move a RUNNING experiment to archive with outcome.

Called when an experiment reaches its evaluation_date. Lisa (or a cron job)
passes the measured post-experiment metric value; this script classifies
the outcome, writes to archive.yml, removes from active.yml, and logs.

Usage:
    python scripts/evaluate_experiment.py 001 --final 44.8

Classification:
    delta_pct >= success_threshold_pct   → WINNER
    delta_pct >= neutral_threshold_pct   → NEUTRAL
    otherwise                            → LOSER
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIVE = REPO_ROOT / "sprints" / "active.yml"
ARCHIVE = REPO_ROOT / "sprints" / "archive.yml"
LOGS = REPO_ROOT / "logs" / "runs.jsonl"


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict, header: str = "") -> None:
    with path.open("w") as f:
        if header:
            f.write(header)
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


def append_log(entry: dict) -> None:
    entry["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with LOGS.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def classify(delta_pct: float, success: float, neutral: float) -> str:
    if delta_pct >= success:
        return "WINNER"
    if delta_pct >= neutral:
        return "NEUTRAL"
    return "LOSER"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment_id")
    ap.add_argument("--final", type=float, required=True, help="Measured post-experiment value")
    ap.add_argument("--note", default="", help="Optional outcome note (e.g. '+$8.2K / 90d')")
    args = ap.parse_args()

    active = load_yaml(ACTIVE)
    archive = load_yaml(ARCHIVE)

    idx = next(
        (i for i, e in enumerate(active.get("experiments", [])) if e.get("id") == args.experiment_id),
        None,
    )
    if idx is None:
        print(f"ERROR: experiment {args.experiment_id} not found in active", file=sys.stderr)
        return 1

    exp = active["experiments"][idx]
    kpi = exp.get("kpi", {})
    evaluation = exp.get("evaluation", {})
    baseline = kpi.get("baseline_value") or 0

    if baseline == 0:
        delta_pct = 100.0 if args.final > 0 else 0.0
    else:
        delta_pct = round((args.final - baseline) / baseline * 100, 1)

    status = classify(
        delta_pct,
        evaluation.get("success_threshold_pct", 10),
        evaluation.get("neutral_threshold_pct", 0),
    )

    # Update the experiment object for archival
    exp["status"] = status
    exp["kpi"]["final_value"] = args.final
    exp["kpi"]["final_display"] = f"{args.final}{'%' if kpi.get('unit') == 'percent' else ''}"
    exp["kpi"]["delta_pct"] = delta_pct
    exp["kpi"]["delta_display"] = f"{'+' if delta_pct >= 0 else ''}{delta_pct}%"
    exp["timeline"]["evaluated"] = date.today().isoformat()
    exp["outcome_note"] = args.note or {
        "WINNER":  f"+{delta_pct}% improvement",
        "NEUTRAL": "below success threshold",
        "LOSER":   "metric declined",
    }[status]

    archive.setdefault("experiments", []).append(exp)
    active["experiments"].pop(idx)

    save_yaml(ACTIVE, active, "# Active experiments — auto-managed.\n\n")
    save_yaml(ARCHIVE, archive, "# Archived experiments — auto-managed.\n\n")

    append_log({
        "run_id": f"sprint-{args.experiment_id}-evaluated",
        "type": "sprint_evaluated",
        "status": "success",
        "sprint_id": args.experiment_id,
        "outcome": status,
        "summary": f"Sprint #{args.experiment_id} evaluated. {kpi.get('display_label')}: {baseline} → {args.final} ({'+' if delta_pct >= 0 else ''}{delta_pct}%). Outcome: {status}.",
    })
    print(f"✓ Experiment {args.experiment_id} archived as {status} (Δ {delta_pct:+.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
