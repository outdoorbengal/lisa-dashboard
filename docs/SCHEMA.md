# YAML schema reference

What Lisa writes into each source file. Scripts read these with strict
expectations, so stick to the shapes below.

---

## `sprints/queue.yml`

A YAML document with one top-level key `sprints:` whose value is a list.
First item in the list = UNACKNOWLEDGED on the dashboard. Second = NEXT_UP.
Additional items are hidden from the main view but live in the details blob.

```yaml
sprints:
  - id: "002"                    # string, zero-padded, globally unique
    created: "2026-04-17"        # ISO date when Lisa added this to the queue
    title: "Add Zeppelin link to cat training guide"
    url: "/blogs/clicker-training-cats/cat-training-guide"
    sprint_type: LINK            # RNK | CTR | CR | LINK

    kpi:
      metric: "referral_sessions_to_zeppelin"   # machine name, for Lisa's tracking
      display_label: "Referral sessions to Zeppelin"
      current_value: 0           # number — the current measured value
      current_display: "~0 now"  # human-readable version shown on bars
      target_value: 25
      target_display: "+25/wk"
      unit: "sessions/week"      # "percent" | "sessions/week" | "count" | etc.

    effort:
      level: LOW                 # LOW | MEDIUM | HIGH
      description: "1 sentence + link"
      estimated_minutes: 15

    rationale: |
      Multi-line explanation. Shows up in the sprint detail modal as
      "Rationale". Explain the evidence behind the recommendation.

    evaluation:
      window_days: 21            # how long to wait after DONE before evaluating
      success_threshold_pct: 10  # Δ >= this% of baseline → WINNER
      neutral_threshold_pct: 0   # Δ >= this% but < success → NEUTRAL; else LOSER

    evidence_urls:               # optional, for audit
      - "https://search.google.com/search-console/..."
```

**Rules:**
- `id` must be unique across queue + active + archive.
- `sprint_type` must be exactly one of `RNK`, `CTR`, `CR`, `LINK`.
- `current_value` and `target_value` are numbers, not strings.
- `rationale` should be a real explanation — not just the title restated.

---

## `sprints/active.yml`

Auto-managed by `sprint_action.py` and `evaluate_experiment.py`.
Lisa should NOT create entries here directly. The only field Lisa
should update between DONE and evaluation is `current_reading`.

```yaml
experiments:
  - id: "001"
    name: "Cannibalization fix — Zeppelin redirect"
    sprint_type: RNK
    status: RUNNING              # only value possible here
    kpi:
      metric: "zeppelin_ctr"
      display_label: "Zeppelin CTR"
      baseline_value: 41.7       # snapshot at DONE-time
      baseline_display: "41.7%"
      target_value: 46.0
      target_display: "46%"
      unit: "percent"
    timeline:
      started: "2026-04-14"
      evaluation_date: "2026-05-26"
      window_days: 42
    url: "/products/zeppelin-harness"
    rationale: "..."             # copied from sprint at DONE-time
    evaluation:
      success_threshold_pct: 10
      neutral_threshold_pct: 0

    # ↓ The one block Lisa should update during the experiment.
    current_reading:
      last_measured: "2026-05-20T06:00:00Z"   # ISO datetime
      current_value: 44.8                     # latest measurement
      days_elapsed: 36
```

When `today >= evaluation_date` AND `current_reading.current_value` is
not null, the daily `evaluate-experiments.yml` workflow archives it.

---

## `sprints/archive.yml`

Auto-managed by `evaluate_experiment.py`. Lisa should never edit.

```yaml
experiments:
  - id: "000"
    # All fields from active.yml, PLUS:
    status: WINNER               # WINNER | NEUTRAL | LOSER
    kpi:
      # ... baseline fields ...
      final_value: 2.4
      final_display: "2.4%"
      delta_pct: 71
      delta_display: "+71%"
    timeline:
      # ... started ...
      evaluated: "2026-04-07"    # ISO date
    outcome_note: "+$8.2K projected / 90d"   # 1-line summary
    evaluation_details: |        # optional long-form
      Statistical significance notes, caveats, etc.
```

---

## `scans/state.yml`

Lisa updates this on every daily scan.

```yaml
current_week:
  week_of: "2026-04-13"          # ISO date of Monday for this week
  pages_total: 283               # total pages Lisa tracks on the site
  daily_target: 40               # ceil(pages_total / 7)

  # Pages scanned per day this week, Monday-indexed.
  # Lisa increments daily_counts[today_index] after each scan.
  daily_counts: [28, 31, 22, 40, 28, 23, 34]

  sources:
    - name: "Google Search Console"
      connected: true            # did the most recent pull succeed?
      last_pull: "2026-04-19T06:00:00Z"
    # ... etc

  opportunities_discovered: 12   # count of pending opportunities in backlog
```

**Weekly rollover (Sunday late or Monday early):**
- `week_of` → new Monday ISO date
- `daily_counts` → `[0, 0, 0, 0, 0, 0, 0]`
- Everything else carries forward

---

## `logs/runs.jsonl`

**Append-only.** One JSON object per line. Never rewrite or reorder.
The dashboard's log viewer reads the last 50 lines.

Minimum required fields:

```json
{
  "ts": "2026-04-19T06:02:14Z",
  "run_id": "scan-20260419-060000",
  "type": "daily_scan",
  "status": "success",
  "summary": "Scanned 34 pages. 2 new opportunities added."
}
```

**`type` values:** `daily_scan`, `sprint_queued`, `sprint_started`,
`sprint_skipped`, `sprint_modified`, `sprint_evaluated`, `error`,
`manual_action`.

**`status` values:** `success`, `warning`, `error`.

**Optional fields:** `duration_ms`, `pages_scanned`, `sources`,
`sprint_id`, `outcome`, `warnings` (array of strings), `reason`, `changes`.

---

## Validation

Before committing, Lisa can sanity-check YAML with:

```bash
python -c "import yaml; yaml.safe_load(open('sprints/queue.yml'))"
```

Or run the full pipeline:

```bash
python scripts/build_data.py
# then inspect public/data.json to confirm it parses as expected
```

If the build workflow ever fails, the most likely cause is malformed YAML.
Check the Actions log for the yaml.YAMLError line number.
