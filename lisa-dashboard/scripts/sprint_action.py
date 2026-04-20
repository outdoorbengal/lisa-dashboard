#!/usr/bin/env python3
"""
sprint_action.py — handle DONE / SKIP / MODIFY actions on a sprint.

Invoked by the sprint-action.yml workflow when the dashboard dispatches
a repository event. All state transitions happen here in code, not in
prose instructions an agent might misread.

Usage:
    python scripts/sprint_action.py done 002
    python scripts/sprint_action.py skip 002 "reason text"
    python scripts/sprint_action.py modify 002 '{"title": "new title"}'
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE = REPO_ROOT / "sprints" / "queue.yml"
ACTIVE = REPO_ROOT / "sprints" / "active.yml"
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


def find_sprint(queue: dict, sprint_id: str) -> tuple[int, dict] | tuple[None, None]:
    for i, sp in enumerate(queue.get("sprints", [])):
        if sp.get("id") == sprint_id:
            return i, sp
    return None, None


def action_done(sprint_id: str) -> int:
    queue = load_yaml(QUEUE)
    active = load_yaml(ACTIVE)

    idx, sprint = find_sprint(queue, sprint_id)
    if idx is None:
        print(f"ERROR: sprint {sprint_id} not found in queue", file=sys.stderr)
        return 1

    # Build the experiment object from the sprint
    kpi = sprint.get("kpi", {})
    evaluation = sprint.get("evaluation", {})
    window_days = evaluation.get("window_days", 28)
    started = date.today()
    eval_date = started + timedelta(days=window_days)

    experiment = {
        "id": sprint["id"],
        "name": sprint["title"],
        "sprint_type": sprint["sprint_type"],
        "status": "RUNNING",
        "kpi": {
            "metric": kpi.get("metric"),
            "display_label": kpi.get("display_label"),
            "baseline_value": kpi.get("current_value"),
            "baseline_display": kpi.get("current_display"),
            "target_value": kpi.get("target_value"),
            "target_display": kpi.get("target_display"),
            "unit": kpi.get("unit"),
        },
        "timeline": {
            "started": started.isoformat(),
            "evaluation_date": eval_date.isoformat(),
            "window_days": window_days,
        },
        "url": sprint.get("url"),
        "rationale": sprint.get("rationale"),
        "evaluation": {
            "success_threshold_pct": evaluation.get("success_threshold_pct", 10),
            "neutral_threshold_pct": evaluation.get("neutral_threshold_pct", 0),
        },
        "current_reading": {
            "last_measured": None,
            "current_value": None,
            "days_elapsed": 0,
        },
    }

    active.setdefault("experiments", []).append(experiment)
    queue["sprints"].pop(idx)

    save_yaml(ACTIVE, active, "# Active experiments — auto-managed. See scripts/sprint_action.py\n\n")
    save_yaml(QUEUE, queue, "# Sprint queue — auto-managed. See scripts/sprint_action.py\n\n")

    append_log({
        "run_id": f"sprint-{sprint_id}-started",
        "type": "sprint_started",
        "status": "success",
        "sprint_id": sprint_id,
        "summary": f"Sprint #{sprint_id} moved from queue to active. Baseline {kpi.get('display_label')}: {kpi.get('current_display')}. Evaluation date: {eval_date.isoformat()}.",
    })
    print(f"✓ Sprint {sprint_id} activated. Evaluates {eval_date.isoformat()}.")
    return 0


def action_skip(sprint_id: str, reason: str = "") -> int:
    queue = load_yaml(QUEUE)
    idx, sprint = find_sprint(queue, sprint_id)
    if idx is None:
        print(f"ERROR: sprint {sprint_id} not found", file=sys.stderr)
        return 1

    queue["sprints"].pop(idx)
    save_yaml(QUEUE, queue, "# Sprint queue — auto-managed.\n\n")

    append_log({
        "run_id": f"sprint-{sprint_id}-skipped",
        "type": "sprint_skipped",
        "status": "success",
        "sprint_id": sprint_id,
        "reason": reason,
        "summary": f"Sprint #{sprint_id} skipped. Reason: {reason or 'none given'}",
    })
    print(f"✓ Sprint {sprint_id} skipped.")
    return 0


def action_modify(sprint_id: str, changes_json: str) -> int:
    queue = load_yaml(QUEUE)
    idx, sprint = find_sprint(queue, sprint_id)
    if idx is None:
        print(f"ERROR: sprint {sprint_id} not found", file=sys.stderr)
        return 1

    try:
        changes = json.loads(changes_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 1

    # Shallow merge — nested fields like kpi.* must be passed as full objects
    queue["sprints"][idx] = {**sprint, **changes}
    save_yaml(QUEUE, queue, "# Sprint queue — auto-managed.\n\n")

    append_log({
        "run_id": f"sprint-{sprint_id}-modified",
        "type": "sprint_modified",
        "status": "success",
        "sprint_id": sprint_id,
        "changes": changes,
        "summary": f"Sprint #{sprint_id} modified: {list(changes.keys())}",
    })
    print(f"✓ Sprint {sprint_id} modified.")
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: sprint_action.py {done|skip|modify} <sprint_id> [arg]", file=sys.stderr)
        return 2

    action = sys.argv[1].lower()
    sprint_id = sys.argv[2]
    extra = sys.argv[3] if len(sys.argv) > 3 else ""

    if action == "done":
        return action_done(sprint_id)
    if action == "skip":
        return action_skip(sprint_id, extra)
    if action == "modify":
        return action_modify(sprint_id, extra)

    print(f"Unknown action: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
