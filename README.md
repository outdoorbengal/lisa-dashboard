# Lisa Dashboard

Dashboard + sprint-approval system for Lisa, an SEO/growth agent working on
OutdoorBengal.com.

**Live:** https://outdoorbengal.github.io/lisa-dashboard/

## What this is

- A static dashboard (GitHub Pages) that shows weekly scan status, running
  experiments, and pending sprints.
- A sprint approval flow: click a sprint card, review the recommendation and
  KPI, click **DONE** to start tracking, **SKIP** to dismiss.
- A run log so you can audit everything Lisa did recently.
- A clean separation between what Lisa's agent decides (proposals, KPI
  readings) and what deterministic scripts do (state transitions, JSON
  generation). The agent never holds GitHub tokens, never hand-builds
  `data.json`, and never directly PUTs to the GitHub API.

## Architecture at a glance

```
  Lisa (agent)                     You (via dashboard)
        │                                  │
        │ edits YAML via git               │ clicks DONE/SKIP/REVIEW
        ▼                                  ▼
  sprints/*.yml              repository_dispatch → sprint-action.yml
  scans/state.yml                            │
  logs/runs.jsonl             scripts/sprint_action.py mutates YAML
        │                                  │
        └────────────── main branch ───────┘
                         │
                         ▼
               build-dashboard.yml
                  │              │
                  ▼              ▼
           public/data.json   GitHub Pages deploy
                  │
                  ▼
             Dashboard
```

## Quick start

See **[docs/SETUP.md](docs/SETUP.md)** for the full deployment guide. TL;DR:

1. Push this repo to GitHub.
2. Enable Pages (Settings → Pages → Source: GitHub Actions).
3. Register a GitHub OAuth App.
4. Deploy the Cloudflare Worker (`docs/oauth-worker.js`).
5. Fill in `public/config.js`.
6. Point Lisa's orchestrator at `docs/LISA_SPEC.md`.

## Key docs

- **[docs/SETUP.md](docs/SETUP.md)** — deployment
- **[docs/LISA_SPEC.md](docs/LISA_SPEC.md)** — operating spec for the agent
- **[docs/SCHEMA.md](docs/SCHEMA.md)** — YAML field reference

## File map

```
.github/workflows/     → 3 GitHub Actions workflows
scripts/               → 3 Python scripts (build, action, evaluate)
sprints/               → queue.yml, active.yml, archive.yml
scans/                 → weekly scan state
logs/                  → append-only run log
public/                → the dashboard (HTML + CSS + JS + generated data.json)
docs/                  → setup, spec, schema, worker source
```

## Design principles

1. **Deterministic JSON.** `data.json` is generated from YAML by a Python
   script. No LLM hand-builds JSON with 30 fields.
2. **No tokens in prompts.** OAuth client_secret lives in a Cloudflare Worker.
   GitHub Actions use the built-in `GITHUB_TOKEN`. The agent never holds
   credentials.
3. **State transitions in code.** Queue → active → archive happens in
   `sprint_action.py` and `evaluate_experiment.py`, not in natural-language
   instructions.
4. **Append-only log.** `logs/runs.jsonl` is never rewritten. This makes
   auditing trivial.
5. **The dashboard renders what exists.** Missing `data.json`? Loading fails
   visibly. Malformed YAML? Build workflow fails visibly. No silent fallbacks
   that mask bugs.
