#!/usr/bin/env python3
"""
draft_copy.py — draft AFTER copy for sprints whose briefs only have BEFORE.

Runs after lisa_sync.py in the daily scan. For every queue sprint whose
execution_brief has a BEFORE (current metadata fetched from the live page)
but no AFTER, this calls Claude to write the proposed SEO title and meta
description, writes them into the brief, and adds the machine-applyable
payload so the dashboard's ACCEPT button works.

Requires ANTHROPIC_API_KEY in the environment; exits quietly (0) when the
key is absent so the scan workflow still succeeds before the secret is set.
Model is configurable via LISA_COPY_MODEL (default: claude-opus-4-8 —
copy quality matters and the volume is a few hundred tokens per sprint).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE = REPO_ROOT / "sprints" / "queue.yml"
LOGS = REPO_ROOT / "logs" / "runs.jsonl"
MAX_DRAFTS_PER_RUN = 5

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lisa_sync import resource_of  # noqa: E402

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "title_tag": {"type": "string", "description": "SEO title, max 60 characters, no brand suffix"},
        "description_tag": {"type": "string", "description": "Meta description, max 155 characters"},
    },
    "required": ["title_tag", "description_tag"],
    "additionalProperties": False,
}

SYSTEM = """You write SEO titles and meta descriptions for OutdoorBengal.com,
a cat adventure-gear store (harnesses, carriers, leashes) run by a professional
cat trainer. Voice: expert, warm, concrete — never clickbait, never ALL CAPS,
no fabricated claims or statistics.

Rules:
- Title ≤ 60 characters. Lead with the phrasing searchers actually type
  (the target query), then a concrete hook. No brand suffix (the theme adds it).
- Description ≤ 155 characters. Answer the searcher's underlying question or
  promise the specific thing the page delivers. Complete sentences.
- Keep any factual claims already present in the current metadata; do not
  invent numbers, dates, or credentials that aren't in the input."""


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — skipping copy drafting.")
        return 0
    try:
        import anthropic
    except ImportError:
        print("anthropic package not installed — skipping copy drafting.", file=sys.stderr)
        return 0

    queue = yaml.safe_load(QUEUE.open()) or {}
    pending = []
    for sp in queue.get("sprints", []):
        brief = sp.get("execution_brief")
        if not brief or brief.get("apply"):
            continue
        steps = brief.get("steps") or []
        if not steps or steps[0].get("after") or not steps[0].get("before"):
            continue
        if (sp.get("_source") or {}).get("dashboard_impact", 0) <= 0:
            continue
        pending.append(sp)

    if not pending:
        print("No sprints need copy drafting.")
        return 0

    client = anthropic.Anthropic()
    model = os.environ.get("LISA_COPY_MODEL", "claude-opus-4-8")
    drafted = []
    for sp in pending[:MAX_DRAFTS_PER_RUN]:
        step = sp["execution_brief"]["steps"][0]
        prompt = (
            f"Page: https://www.outdoorbengal.com{sp['url']}\n"
            f"Sprint: {sp['title']}\n"
            f"Why it was flagged: {sp.get('rationale', '')[:600]}\n\n"
            f"Current metadata (BEFORE):\n{step['before']}\n\n"
            "Write the replacement SEO title and meta description."
        )
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": COPY_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            print(f"  ! sprint {sp['id']}: model refused, skipping", file=sys.stderr)
            continue
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            copy = json.loads(text)
        except json.JSONDecodeError:
            print(f"  ! sprint {sp['id']}: unparseable output, skipping", file=sys.stderr)
            continue
        title, desc = copy["title_tag"].strip(), copy["description_tag"].strip()
        step["after"] = f"Title: {title}\nDescription: {desc}"
        step.setdefault("note", "")
        step["note"] = (step["note"] + " Drafted by Claude — review, then ACCEPT to apply.").strip()
        res = resource_of(sp["url"])
        if res:
            sp["execution_brief"]["apply"] = {
                **res, "set": {"title_tag": title, "description_tag": desc},
            }
        drafted.append(sp["id"])
        print(f"  ✓ drafted copy for sprint {sp['id']}: {title}")

    if not drafted:
        return 0

    with QUEUE.open("w") as f:
        f.write("# Sprint queue — managed by lisa_sync.py.\n# Ordered by dashboard_impact (estimated $/month, highest first).\n# First item = UNACKNOWLEDGED on the dashboard.\n\n")
        yaml.safe_dump(queue, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    with LOGS.open("a") as f:
        f.write(json.dumps({
            "run_id": f"draft-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "type": "manual_action", "status": "success",
            "summary": f"Claude ({model}) drafted metadata copy for sprint(s) {', '.join(drafted)} — pending review/ACCEPT.",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
