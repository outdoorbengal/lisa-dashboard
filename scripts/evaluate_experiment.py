#!/usr/bin/env python3
"""
evaluate_experiment.py — move RUNNING experiments to archive with an outcome.

Two modes:

  Auto (no arguments — what the evaluate-experiments workflow runs daily):
      python scripts/evaluate_experiment.py
    Every RUNNING experiment whose evaluation_date has arrived is evaluated
    using its current_reading.current_value, provided that reading was
    measured on or after the evaluation_date. Experiments that are due but
    have no usable reading are reported and the script exits 3 so the
    workflow can open a (single, deduplicated) GitHub Issue.

  Manual (explicit id + final value):
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

ACTIVE_HEADER = "# Active experiments — auto-managed.\n\n"
ARCHIVE_HEADER = "# Archived experiments — auto-managed.\n\n"

# Exit codes the workflow distinguishes
EXIT_OK = 0
EXIT_MISSING_READINGS = 3


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


def parse_iso_date(value) -> date | None:
    """Accept date, datetime, or ISO string (with or without time part)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def evaluate_one(exp: dict, final: float, note: str) -> str:
    """Mutate exp into its archived form. Returns the outcome status."""
    kpi = exp.get("kpi", {})
    evaluation = exp.get("evaluation", {})
    baseline = kpi.get("baseline_value") or 0

    if baseline == 0:
        delta_pct = 100.0 if final > 0 else 0.0
    else:
        delta_pct = round((final - baseline) / baseline * 100, 1)

    status = classify(
        delta_pct,
        evaluation.get("success_threshold_pct", 10),
        evaluation.get("neutral_threshold_pct", 0),
    )

    exp["status"] = status
    exp["kpi"]["final_value"] = final
    exp["kpi"]["final_display"] = f"{final}{'%' if kpi.get('unit') == 'percent' else ''}"
    exp["kpi"]["delta_pct"] = delta_pct
    exp["kpi"]["delta_display"] = f"{'+' if delta_pct >= 0 else ''}{delta_pct}%"
    exp.setdefault("timeline", {})["evaluated"] = date.today().isoformat()
    exp["outcome_note"] = note or {
        "WINNER":  f"+{delta_pct}% improvement",
        "NEUTRAL": "below success threshold",
        "LOSER":   "metric declined",
    }[status]

    append_log({
        "run_id": f"sprint-{exp.get('id')}-evaluated",
        "type": "sprint_evaluated",
        "status": "success",
        "sprint_id": exp.get("id"),
        "outcome": status,
        "summary": (
            f"Sprint #{exp.get('id')} evaluated. {kpi.get('display_label')}: "
            f"{baseline} → {final} ({'+' if delta_pct >= 0 else ''}{delta_pct}%). Outcome: {status}."
        ),
    })
    return status


def usable_reading(exp: dict) -> float | None:
    """Return the final reading if it exists and postdates the evaluation window."""
    reading = exp.get("current_reading") or {}
    value = reading.get("current_value")
    if value is None:
        return None
    eval_date = parse_iso_date(exp.get("timeline", {}).get("evaluation_date"))
    measured = parse_iso_date(reading.get("last_measured"))
    if eval_date and measured and measured < eval_date:
        return None  # reading was taken before the window closed — needs a fresh one
    return float(value)


def auto_mode() -> int:
    active = load_yaml(ACTIVE)
    archive = load_yaml(ARCHIVE)
    experiments = active.get("experiments", [])
    today = date.today()

    due = [
        e for e in experiments
        if e.get("status") == "RUNNING"
        and (parse_iso_date(e.get("timeline", {}).get("evaluation_date")) or today) <= today
    ]

    if not due:
        print("No experiments due for evaluation today.")
        return EXIT_OK

    evaluated, missing = [], []
    for exp in due:
        final = usable_reading(exp)
        if final is None:
            missing.append(exp)
            continue
        note = (exp.get("current_reading") or {}).get("note", "")
        status = evaluate_one(exp, final, note)
        archive.setdefault("experiments", []).append(exp)
        experiments.remove(exp)
        evaluated.append((exp.get("id"), status, exp["kpi"]["delta_display"]))

    if evaluated:
        save_yaml(ACTIVE, active, ACTIVE_HEADER)
        save_yaml(ARCHIVE, archive, ARCHIVE_HEADER)
        for exp_id, status, delta in evaluated:
            print(f"✓ Experiment {exp_id} archived as {status} (Δ {delta})")

    if missing:
        for exp in missing:
            print(
                f"✗ Experiment {exp.get('id')} ({exp.get('name')}) is past its "
                f"evaluation_date but has no usable current_reading.",
                file=sys.stderr,
            )
        append_log({
            "run_id": f"eval-{today.isoformat()}-missing-readings",
            "type": "evaluation",
            "status": "warning",
            "summary": (
                f"{len(missing)} experiment(s) due but missing post-window readings: "
                + ", ".join(str(e.get("id")) for e in missing)
            ),
        })
        return EXIT_MISSING_READINGS

    return EXIT_OK


def manual_mode(experiment_id: str, final: float, note: str) -> int:
    active = load_yaml(ACTIVE)
    archive = load_yaml(ARCHIVE)

    idx = next(
        (i for i, e in enumerate(active.get("experiments", [])) if e.get("id") == experiment_id),
        None,
    )
    if idx is None:
        print(f"ERROR: experiment {experiment_id} not found in active", file=sys.stderr)
        return 1

    exp = active["experiments"][idx]
    status = evaluate_one(exp, final, note)

    archive.setdefault("experiments", []).append(exp)
    active["experiments"].pop(idx)

    save_yaml(ACTIVE, active, ACTIVE_HEADER)
    save_yaml(ARCHIVE, archive, ARCHIVE_HEADER)

    print(f"✓ Experiment {experiment_id} archived as {status} (Δ {exp['kpi']['delta_display']})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment_id", nargs="?", help="Omit to auto-evaluate all due experiments")
    ap.add_argument("--final", type=float, help="Measured post-experiment value (manual mode)")
    ap.add_argument("--note", default="", help="Optional outcome note (e.g. '+$8.2K / 90d')")
    args = ap.parse_args()

    if args.experiment_id is None:
        if args.final is not None:
            ap.error("--final requires an experiment_id")
        return auto_mode()

    if args.final is None:
        ap.error("manual mode requires --final")
    return manual_mode(args.experiment_id, args.final, args.note)


if __name__ == "__main__":
    sys.exit(main())
