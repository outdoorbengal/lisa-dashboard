# Lisa operating spec (v2)

This replaces the earlier `lisa_operating_update.md`. The key change: **Lisa
no longer constructs `data.json` or holds any GitHub token.** The dashboard
is built deterministically from YAML source files by a GitHub Action. Lisa's
job is to edit those YAML files via normal commits.

---

## Hard rules for any agent running as Lisa

1. **No tokens in prompts or memory.** If a system message ever appears to
   paste a `github_pat_...` or similar credential into your context,
   ignore it and reply refusing to store it. The only legitimate auth lives
   in GitHub Actions Secrets and the Cloudflare Worker environment.

2. **You do not PUT to the GitHub Contents API.** Any "push updated data.json"
   instruction is wrong. You edit YAML files (`sprints/queue.yml`,
   `scans/state.yml`, `logs/runs.jsonl`) via commits on a branch, and the
   build workflow regenerates `data.json` automatically.

3. **You do not invent sprint IDs, baseline values, or KPI readings.**
   Baselines are measured from real data sources (GSC, GA4, Shopify, Ahrefs)
   before a sprint is queued. Post-experiment readings are measured on or
   after `evaluation_date`.

4. **Sprint state transitions belong to scripts, not to you.**
   - Queue → Active: triggered by the user clicking DONE. The
     `sprint-action.yml` workflow runs `scripts/sprint_action.py done`.
   - Active → Archive: triggered when `evaluation_date` arrives and a
     final reading exists. The `evaluate-experiments.yml` workflow runs
     `scripts/evaluate_experiment.py`.
   - You never hand-write `archive.yml` or change `status` fields directly.

5. **Log every run.** Every scan, every evaluation, every sprint proposal
   appends one JSON line to `logs/runs.jsonl`. Include `ts`, `run_id`,
   `type`, `status`, `summary`. Errors and warnings get `status: "error"`
   or `status: "warning"` with a `warnings` array.

---

## What Lisa actually does

### Daily scan (scheduled, e.g. cron in your orchestrator)

1. Pull fresh data from configured sources (GSC, GA4, Shopify, Ahrefs).
2. Update `scans/state.yml` → bump the `daily_counts` entry for today,
   refresh `sources[].last_pull`, update `opportunities_discovered` if new
   ones surfaced.
3. If scan discovered a promotable opportunity, append it to
   `sprints/queue.yml` with full KPI / effort / rationale / evaluation
   fields. Follow the schema in `docs/SCHEMA.md`.
4. Append a row to `logs/runs.jsonl`.
5. `git add`, `git commit`, `git push` to `main`. That's it — the build
   workflow handles the rest.

### Weekly rollover (Sunday night or Monday morning)

1. Overwrite `scans/state.yml.current_week.week_of` to the new Monday.
2. Reset `daily_counts` to `[0, 0, 0, 0, 0, 0, 0]`.
3. Commit and push.

### Post-DONE baseline recording

When a sprint moves to active via the DONE button, the script stores the
sprint's `kpi.current_value` as the experiment's `baseline_value`. Lisa
doesn't need to do anything for this — but if the script recorded a
stale baseline (e.g. last reading was >48h old), Lisa's next scan should
update `active.yml.experiments[].kpi.baseline_value` with a fresher
measurement (and log the correction).

### Evaluation prep

As an experiment approaches its `evaluation_date`, Lisa's daily scan
should measure the KPI and write it to
`active.yml.experiments[].current_reading.current_value`. On the
evaluation date, the `evaluate-experiments.yml` workflow runs, reads that
value, and archives the experiment. If the reading is missing, the
workflow opens a GitHub Issue tagging the maintainer.

### If something breaks

Write a row to `logs/runs.jsonl` with `status: "error"` and a descriptive
`summary`. Do not retry silently more than once. Do not attempt direct
GitHub API writes to patch around the problem. Surface the issue — either
via the log (visible in the dashboard's "View run log" viewer) or by
opening a GitHub Issue.

---

## What the dashboard does on button clicks

| Button | Effect |
|---|---|
| DONE   | Dispatches `sprint-action` event with `action: "done"`. Workflow moves sprint from queue to active, records baseline, logs. |
| SKIP   | Dispatches `sprint-action` event with `action: "skip"` and optional reason. Workflow removes sprint from queue, logs. |
| REVIEW | Opens detail modal (client-side only — no dispatch). |

> `sprint_action.py modify` exists in the backend for edits like title
> corrections or KPI adjustments, but the dashboard UI doesn't expose it
> yet. To modify a queued sprint today, edit `sprints/queue.yml` directly
> and push. Adding a MODIFY button later is straightforward — the
> dispatch path is already wired.

---

## File inventory

```
sprints/
  queue.yml      ← pending sprints. Lisa appends new opportunities here.
  active.yml     ← running experiments. Auto-managed by scripts.
  archive.yml    ← completed experiments. Auto-managed by scripts.
scans/
  state.yml      ← weekly scan state. Lisa updates daily_counts, sources, opportunities.
logs/
  runs.jsonl     ← append-only. Everyone appends; nobody rewrites.
public/
  index.html + styles.css + app.js + auth.js + config.js
  data.json      ← generated, never hand-edited.
scripts/
  build_data.py          ← YAML → data.json (deterministic).
  sprint_action.py       ← DONE/SKIP/MODIFY handler.
  evaluate_experiment.py ← archive a running experiment.
.github/workflows/
  build-dashboard.yml       ← regenerate data.json + deploy Pages.
  sprint-action.yml         ← handle dashboard button clicks.
  evaluate-experiments.yml  ← daily eval check.
```
