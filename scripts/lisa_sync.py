#!/usr/bin/env python3
"""
lisa_sync.py — Lisa's daily scan (rebuilt 2026-07; the original ran on a
server that is gone).

Pulls Google Search Console, GA4 and Shopify, then updates the YAML source
files the dashboard is built from:

  scans/state.yml       daily counts, source freshness, weekly rollover
  sprints/queue.yml     re-measures KPIs, re-scores, appends new opportunities
  sprints/active.yml    refreshes current_reading on RUNNING experiments
  logs/runs.jsonl       one appended row per run

It never touches git — the lisa-scan workflow (or you, locally) commits.
Credentials come from the environment; locally they're loaded from
.env.local (git-ignored). Run with --dry-run to see what would change.

Scoring: every opportunity gets a dashboard_impact = estimated incremental
revenue per month in USD, computed from GSC CTR-curve gaps and GA4
conversion/AOV data. The queue is sorted by it, highest first.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE = REPO_ROOT / "sprints" / "queue.yml"
ACTIVE = REPO_ROOT / "sprints" / "active.yml"
ARCHIVE = REPO_ROOT / "sprints" / "archive.yml"
SCANS = REPO_ROOT / "scans" / "state.yml"
LOGS = REPO_ROOT / "logs" / "runs.jsonl"

WINDOW_DAYS = 28          # measurement window for all metrics
MAX_NEW_PER_RUN = 3       # don't flood the queue
MIN_IMPACT_USD = 50.0     # new opportunities below this $/month are ignored
FALLBACK_AOV = 60.0
EXCLUDED_PREFIXES = ("/cart", "/checkout", "/account", "/policies", "/search", "/password", "/pages/contact")

# Rough organic CTR by position — used to spot underperformers and size upside.
CTR_CURVE = {1: .28, 2: .15, 3: .10, 4: .07, 5: .05, 6: .04, 7: .03, 8: .025, 9: .02, 10: .018}


def expected_ctr(position: float) -> float:
    p = max(1, round(position))
    if p in CTR_CURVE:
        return CTR_CURVE[p]
    return .012 if p <= 20 else .005


# ── env ──────────────────────────────────────────────────────────────
def load_env() -> dict:
    """Environment wins; .env.local fills gaps for local runs."""
    import os
    env = dict(os.environ)
    dotenv = REPO_ROOT / ".env.local"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    missing = [k for k in (
        "SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS", "GSC_SITE_URL", "GA4_PROPERTY_ID",
    ) if not env.get(k)]
    if missing:
        sys.exit(f"Missing credentials: {', '.join(missing)} (set env vars or .env.local)")
    return env


# ── path normalization ───────────────────────────────────────────────
def norm_path(url_or_path: str) -> str:
    p = urlparse(url_or_path).path or "/"
    p = re.sub(r"/+$", "", p) or "/"
    return p.lower()


def is_scannable(path: str) -> bool:
    return path != "/" and not any(path.startswith(x) for x in EXCLUDED_PREFIXES)


# ── data sources ─────────────────────────────────────────────────────
def fetch_gsc(env: dict, headers: dict, start: date, end: date) -> dict:
    """{path: {clicks, impressions, ctr, position}} over the window."""
    site = quote(env["GSC_SITE_URL"], safe="")
    r = requests.post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query",
        headers=headers,
        json={"startDate": start.isoformat(), "endDate": end.isoformat(),
              "dimensions": ["page"], "rowLimit": 25000, "dataState": "final"},
        timeout=60,
    )
    r.raise_for_status()
    pages = {}
    for row in r.json().get("rows", []):
        path = norm_path(row["keys"][0])
        agg = pages.setdefault(path, {"clicks": 0.0, "impressions": 0.0, "pos_x_imp": 0.0})
        agg["clicks"] += row["clicks"]
        agg["impressions"] += row["impressions"]
        agg["pos_x_imp"] += row["position"] * row["impressions"]
    for p, a in pages.items():
        a["ctr"] = a["clicks"] / a["impressions"] if a["impressions"] else 0.0
        a["position"] = a["pos_x_imp"] / a["impressions"] if a["impressions"] else 0.0
        del a["pos_x_imp"]
    return pages


def fetch_gsc_top_query(env: dict, headers: dict, path: str, start: date, end: date) -> str:
    site = quote(env["GSC_SITE_URL"], safe="")
    try:
        r = requests.post(
            f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query",
            headers=headers,
            json={"startDate": start.isoformat(), "endDate": end.isoformat(),
                  "dimensions": ["query"], "rowLimit": 1,
                  "dimensionFilterGroups": [{"filters": [
                      {"dimension": "page", "operator": "contains", "expression": path}]}]},
            timeout=30,
        )
        rows = r.json().get("rows", [])
        return rows[0]["keys"][0] if rows else ""
    except Exception:
        return ""


def fetch_ga4(env: dict, headers: dict, start: date, end: date) -> tuple[dict, dict]:
    """({path: {sessions, purchases, revenue}}, site_totals) over the window."""
    r = requests.post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{env['GA4_PROPERTY_ID']}:runReport",
        headers=headers,
        json={"dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
              "dimensions": [{"name": "landingPage"}],
              "metrics": [{"name": "sessions"}, {"name": "ecommercePurchases"}, {"name": "purchaseRevenue"}],
              "limit": 25000},
        timeout=60,
    )
    r.raise_for_status()
    pages, totals = {}, {"sessions": 0.0, "purchases": 0.0, "revenue": 0.0}
    for row in r.json().get("rows", []):
        path = norm_path(row["dimensionValues"][0]["value"] or "/")
        vals = [float(m["value"] or 0) for m in row["metricValues"]]
        agg = pages.setdefault(path, {"sessions": 0.0, "purchases": 0.0, "revenue": 0.0})
        agg["sessions"] += vals[0]
        agg["purchases"] += vals[1]
        agg["revenue"] += vals[2]
        totals["sessions"] += vals[0]
        totals["purchases"] += vals[1]
        totals["revenue"] += vals[2]
    return pages, totals


def fetch_shopify(env: dict) -> dict:
    """Mint a client-credentials token, return {'aov': float, 'products': int}."""
    domain = env["SHOPIFY_STORE_DOMAIN"]
    tok = requests.post(
        f"https://{domain}/admin/oauth/access_token",
        json={"grant_type": "client_credentials",
              "client_id": env["SHOPIFY_CLIENT_ID"],
              "client_secret": env["SHOPIFY_CLIENT_SECRET"]},
        timeout=30,
    )
    tok.raise_for_status()
    headers = {"X-Shopify-Access-Token": tok.json()["access_token"]}
    since = (date.today() - timedelta(days=60)).isoformat()
    orders = requests.get(
        f"https://{domain}/admin/api/2025-04/orders.json",
        params={"status": "any", "created_at_min": since, "limit": 250, "fields": "total_price"},
        headers=headers, timeout=60,
    )
    orders.raise_for_status()
    totals = [float(o["total_price"]) for o in orders.json().get("orders", [])]
    count = requests.get(f"https://{domain}/admin/api/2025-04/products/count.json",
                         headers=headers, timeout=30)
    return {
        "aov": round(sum(totals) / len(totals), 2) if totals else FALLBACK_AOV,
        "products": count.json().get("count", 0) if count.status_code == 200 else 0,
    }


# ── measurement (shared by queue refresh and experiment readings) ────
def measure(metric: str, path: str, gsc: dict, ga4: dict, ga4_totals: dict):
    """Returns (value, display, note) or None if this metric isn't measurable."""
    g = gsc.get(path)
    a = ga4.get(path)
    if metric == "organic_clicks_weekly":
        if not g:
            return None
        v = round(g["clicks"] / (WINDOW_DAYS / 7), 1)
        return v, f"~{v:g}/wk now", f"{int(g['clicks'])} clicks / {WINDOW_DAYS}d · pos {g['position']:.1f}"
    if metric in ("ctr", "zeppelin_ctr"):
        if not g:
            return None
        v = round(g["ctr"] * 100, 2)
        return v, f"{v:g}% now", f"{int(g['clicks'])} clicks / {int(g['impressions'])} impressions"
    if metric == "conversion_rate":
        if not a or a["sessions"] < 30:
            return None
        v = round(a["purchases"] / a["sessions"] * 100, 2)
        return v, f"{v:g}% now", f"{int(a['purchases'])} purchases / {int(a['sessions'])} sessions ({WINDOW_DAYS}d)"
    if metric in ("sessions_28d", "consolidated_page_clicks"):
        if not a:
            return None
        v = round(a["sessions"], 0)
        return v, f"{int(v)} now", f"sessions {WINDOW_DAYS}d"
    return None  # unknown metric (e.g. referral_sessions_to_zeppelin) — leave untouched


# ── impact scoring: estimated incremental $ per month ────────────────
def page_cr(path: str, ga4: dict, ga4_totals: dict) -> float:
    a = ga4.get(path)
    if a and a["sessions"] >= 100 and a["purchases"] > 0:
        return a["purchases"] / a["sessions"]
    return (ga4_totals["purchases"] / ga4_totals["sessions"]) if ga4_totals["sessions"] else 0.01


def score_impact(sprint_type: str, path: str, gsc: dict, ga4: dict, ga4_totals: dict, aov: float):
    g, a = gsc.get(path), ga4.get(path)
    month = 30 / WINDOW_DAYS
    cr = page_cr(path, ga4, ga4_totals)
    if sprint_type == "CTR" and g:
        gap = max(expected_ctr(g["position"]) - g["ctr"], 0)
        return round(g["impressions"] * month * gap * cr * aov, 2)
    if sprint_type == "RNK" and g:
        target_pos = max(g["position"] - 5, 4)
        gap = max(expected_ctr(target_pos) - g["ctr"], 0)
        return round(g["impressions"] * month * gap * cr * aov, 2)
    if sprint_type == "CR" and a and a["sessions"] > 0:
        current = a["purchases"] / a["sessions"]
        target = max(current * 1.5, 0.01)
        return round(a["sessions"] * month * (target - current) * aov, 2)
    return None  # unscoreable (LINK plays etc.) — keep previous score


# ── revenue model (what build_data.py ranks and renders from) ────────
def build_revenue_model(sprint_type: str, path: str, gsc: dict, ga4: dict, ga4_totals: dict, aov: float):
    """Emit the queue.yml revenue_model block build_data.py expects.
    Sprints without one fall to the bottom of the dashboard ranking."""
    g, a = gsc.get(path), ga4.get(path)
    month = 30 / WINDOW_DAYS
    cr = page_cr(path, ga4, ga4_totals)
    if sprint_type in ("CTR", "RNK"):
        if not g:
            return None
        current = {
            "impressions_30d": int(g["impressions"] * month),
            "ctr": round(g["ctr"], 4),
            "sessions_30d": int(a["sessions"] * month) if a else int(g["clicks"] * month),
            "conversion_rate": round(cr, 4),
            "aov": round(aov, 2),
        }
        if sprint_type == "CTR":
            projected = {"ctr": round(expected_ctr(g["position"]), 4)}
            confidence = "medium"
        else:
            target_pos = max(g["position"] - 5, 4)
            projected = {"impressions_multiplier": 2.0, "ctr": round(expected_ctr(target_pos), 4)}
            confidence = "low"
        return {"current": current, "projected": projected, "confidence": confidence}
    if sprint_type == "CR":
        if not a or a["sessions"] < 30:
            return None
        current = {
            "impressions_30d": int(g["impressions"] * month) if g else 0,
            "ctr": round(g["ctr"], 4) if g else 0.0,
            "sessions_30d": int(a["sessions"] * month),
            "conversion_rate": round(a["purchases"] / a["sessions"], 4),
            "aov": round(aov, 2),
        }
        projected = {"conversion_rate": round(max(a["purchases"] / a["sessions"] * 1.5, 0.01), 4)}
        return {"current": current, "projected": projected, "confidence": "medium"}
    return None


# ── opportunity detection ────────────────────────────────────────────
def detect_opportunities(gsc, ga4, ga4_totals, aov, taken: set):
    found = []
    for path, g in gsc.items():
        if not is_scannable(path):
            continue
        if g["impressions"] >= 1000 and g["position"] <= 12 and g["ctr"] < 0.5 * expected_ctr(g["position"]):
            if (path, "CTR") not in taken:
                found.append(("CTR", path))
        if 8 <= g["position"] <= 20 and g["impressions"] >= 500:
            if (path, "RNK") not in taken:
                found.append(("RNK", path))
    for path, a in ga4.items():
        if not is_scannable(path) or not path.startswith("/products/"):
            continue
        if a["sessions"] >= 300 and (a["purchases"] / a["sessions"]) < 0.01:
            if (path, "CR") not in taken:
                found.append(("CR", path))
    scored = []
    for stype, path in found:
        impact = score_impact(stype, path, gsc, ga4, ga4_totals, aov)
        if impact and impact >= MIN_IMPACT_USD:
            scored.append((impact, stype, path))
    scored.sort(reverse=True)
    return scored[:MAX_NEW_PER_RUN]


def build_sprint(next_id: int, stype: str, path: str, gsc, ga4, ga4_totals, aov, impact, top_query, now_iso):
    g, a = gsc.get(path, {}), ga4.get(path, {})
    slug = path.rstrip("/").split("/")[-1] or path
    cr = page_cr(path, ga4, ga4_totals)
    evidence = (f"pos={g.get('position', 0):.1f} | impressions={int(g.get('impressions', 0))} | "
                f"clicks={int(g.get('clicks', 0))} | CTR={g.get('ctr', 0) * 100:.1f}% | "
                f"sessions={int(a.get('sessions', 0))} | CR={cr * 100:.2f}% | AOV=${aov:g}")
    titles = {
        "CTR": f"Fix title/meta — {slug} ({int(g.get('impressions', 0))} impressions)",
        "RNK": f"Push to page 1 — {slug}",
        "CR": f"Conversion fix — {slug}",
    }
    rationales = {
        "CTR": f"Ranking pos {g.get('position', 0):.1f}"
               + (f" for '{top_query}'" if top_query else "")
               + f" but CTR is {g.get('ctr', 0) * 100:.1f}% vs ~{expected_ctr(g.get('position', 10)) * 100:.0f}% expected — title/meta underselling the click",
        "RNK": f"Ranking pos {g.get('position', 0):.1f}"
               + (f" for '{top_query}'" if top_query else "")
               + f" — {int(g.get('impressions', 0))} impressions stuck on page 2",
        "CR": f"{int(a.get('sessions', 0))} sessions/{WINDOW_DAYS}d but only {cr * 100:.2f}% conversion — revenue leak",
    }
    effort = {
        "CTR": {"level": "LOW", "description": "Title + meta rewrite only", "estimated_minutes": 20},
        "RNK": {"level": "MEDIUM", "description": "Copy + internal links", "estimated_minutes": 45},
        "CR": {"level": "MEDIUM", "description": "PDP copy / offer / trust review", "estimated_minutes": 60},
    }[stype]
    if stype == "CR":
        kpi = {"metric": "conversion_rate", "display_label": f"Conversion rate · {int(a.get('sessions', 0))} sessions",
               "current_value": round(cr * 100, 2), "current_display": f"{cr * 100:.2f}% now",
               "target_value": round(max(cr * 1.5, 0.01) * 100, 2),
               "target_display": f"{max(cr * 1.5, 0.01) * 100:.2f}%", "unit": "percent"}
    elif stype == "CTR":
        v = round(g.get("ctr", 0) * 100, 2)
        tgt = round(expected_ctr(g.get("position", 10)) * 100, 2)
        kpi = {"metric": "ctr", "display_label": f"CTR · {int(g.get('impressions', 0))} impressions · pos {g.get('position', 0):.1f}",
               "current_value": v, "current_display": f"{v:g}% now",
               "target_value": tgt, "target_display": f"{tgt:g}%+", "unit": "percent"}
    else:
        wk = round(g.get("clicks", 0) / (WINDOW_DAYS / 7), 1)
        kpi = {"metric": "organic_clicks_weekly", "display_label": f"Weekly clicks · pos {g.get('position', 0):.1f}",
               "current_value": wk, "current_display": f"~{wk:g}/wk now",
               "target_value": round(max(wk * 3, wk + 4), 1), "target_display": f"+{round(max(wk * 2, 4), 1):g}/wk",
               "unit": "sessions/week"}
    return {
        "id": f"{next_id:03d}",
        "created": date.today().isoformat(),
        "title": titles[stype],
        "url": path,
        "sprint_type": stype,
        "kpi": kpi,
        "effort": effort,
        "rationale": f"{rationales[stype]}\n\nEvidence: {evidence}\nDashboard impact: ${impact:g}/mo estimated",
        "evaluation": {"window_days": 42 if stype != "CR" else 28,
                       "success_threshold_pct": 15 if stype != "CR" else 25,
                       "neutral_threshold_pct": 0},
        "evidence_urls": [],
        "_source": {"opportunity_type": {"CTR": "SEARCH_VISIBILITY", "RNK": "TRAFFIC_GROWTH", "CR": "CONVERSION_OPTIMIZATION"}[stype],
                    "detected_at": now_iso, "last_refreshed": now_iso, "dashboard_impact": impact},
        "revenue_model": build_revenue_model(stype, path, gsc, ga4, ga4_totals, aov),
    }


# ── yaml plumbing ────────────────────────────────────────────────────
def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict, header: str) -> None:
    with path.open("w") as f:
        f.write(header)
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


def append_log(entry: dict) -> None:
    with LOGS.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def all_ids(*docs) -> list[int]:
    ids = []
    for doc, key in docs:
        for item in doc.get(key, []):
            try:
                ids.append(int(item.get("id", 0)))
            except ValueError:
                pass
    return ids


# ── main ─────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Fetch + compute, but write nothing")
    args = ap.parse_args()

    t0 = time.time()
    env = load_env()
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = date.today() - timedelta(days=2)   # GSC data lags ~2 days
    start = end - timedelta(days=WINDOW_DAYS - 1)

    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_file(
        env["GOOGLE_APPLICATION_CREDENTIALS"],
        scopes=["https://www.googleapis.com/auth/webmasters.readonly",
                "https://www.googleapis.com/auth/analytics.readonly"])
    creds.refresh(Request())
    gheaders = {"Authorization": f"Bearer {creds.token}"}

    gsc = fetch_gsc(env, gheaders, start, end)
    ga4, ga4_totals = fetch_ga4(env, gheaders, start, end)
    shop = fetch_shopify(env)
    aov = shop["aov"]
    pages_scanned = len(set(gsc) | set(ga4))
    print(f"Pulled: {len(gsc)} GSC pages, {len(ga4)} GA4 landing pages, AOV ${aov:g}")

    queue = load_yaml(QUEUE)
    active = load_yaml(ACTIVE)
    archive = load_yaml(ARCHIVE)
    scans = load_yaml(SCANS)

    # 1. Refresh queue KPIs + scores
    reranked = 0
    for sp in queue.get("sprints", []):
        path = norm_path(sp.get("url", ""))
        m = measure(sp["kpi"].get("metric", ""), path, gsc, ga4, ga4_totals)
        if m:
            sp["kpi"]["current_value"], sp["kpi"]["current_display"], _ = m
        impact = score_impact(sp.get("sprint_type", ""), path, gsc, ga4, ga4_totals, aov)
        src = sp.setdefault("_source", {})
        if impact is not None:
            src["dashboard_impact"] = impact
        src["last_refreshed"] = now_iso
        # Keep the revenue model's inputs live: refresh `current` with today's
        # measurements (the dashboard ranks by this), add the whole block for
        # entries that never had one, and leave `projected` as authored.
        rm = build_revenue_model(sp.get("sprint_type", ""), path, gsc, ga4, ga4_totals, aov)
        if rm:
            existing = sp.get("revenue_model")
            if isinstance(existing, dict) and existing.get("projected"):
                existing["current"] = rm["current"]
            else:
                sp["revenue_model"] = rm
        if m or impact is not None:
            reranked += 1
    queue.get("sprints", []).sort(key=lambda s: s.get("_source", {}).get("dashboard_impact", 0), reverse=True)

    # 2. New opportunities
    taken = {(norm_path(s.get("url", "")), s.get("sprint_type")) for s in queue.get("sprints", [])}
    taken |= {(norm_path(e.get("url", "")), e.get("sprint_type")) for e in active.get("experiments", [])}
    taken |= {(norm_path(e.get("url", "")), e.get("sprint_type")) for e in archive.get("experiments", [])}
    next_id = max(all_ids((queue, "sprints"), (active, "experiments"), (archive, "experiments")), default=0) + 1
    new_sprints = []
    for impact, stype, path in detect_opportunities(gsc, ga4, ga4_totals, aov, taken):
        top_query = fetch_gsc_top_query(env, gheaders, path, start, end) if stype in ("CTR", "RNK") else ""
        new_sprints.append(build_sprint(next_id, stype, path, gsc, ga4, ga4_totals, aov, impact, top_query, now_iso))
        next_id += 1
    if new_sprints:
        queue.setdefault("sprints", []).extend(new_sprints)
        queue["sprints"].sort(key=lambda s: s.get("_source", {}).get("dashboard_impact", 0), reverse=True)

    # 3. Experiment readings
    measured = 0
    for exp in active.get("experiments", []):
        if exp.get("status") != "RUNNING":
            continue
        path = norm_path(exp.get("url", ""))
        m = measure(exp.get("kpi", {}).get("metric", ""), path, gsc, ga4, ga4_totals)
        if not m:
            continue
        started = exp.get("timeline", {}).get("started")
        days = (date.today() - date.fromisoformat(str(started)[:10])).days if started else 0
        exp["current_reading"] = {"last_measured": now_iso, "current_value": m[0],
                                  "days_elapsed": days, "note": m[2]}
        measured += 1

    # 4. Scan state (with Monday rollover)
    monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    cw = scans.setdefault("current_week", {})
    if cw.get("week_of") != monday:
        cw["week_of"] = monday
        cw["daily_counts"] = [0] * 7
    counts = cw.setdefault("daily_counts", [0] * 7)
    counts[date.today().weekday()] = pages_scanned
    cw["pages_total"] = pages_scanned
    cw["daily_target"] = math.ceil(pages_scanned / 7) if pages_scanned else 40
    cw["sources"] = [
        {"name": "Google Search Console", "connected": True, "last_pull": now_iso},
        {"name": "Google Analytics 4", "connected": True, "last_pull": now_iso},
        {"name": "Shopify Admin API", "connected": True, "last_pull": now_iso},
    ]
    cw["opportunities_discovered"] = len(queue.get("sprints", []))

    summary = (f"Scanned {pages_scanned} pages. {len(new_sprints)} new opportunities, "
               f"{reranked} reranked. {measured} active experiments re-measured.")
    print(summary)
    for sp in new_sprints:
        print(f"  + [{sp['sprint_type']}] {sp['title']} (${sp['_source']['dashboard_impact']:g}/mo)")

    if args.dry_run:
        print("(dry run — nothing written)")
        return 0

    save_yaml(QUEUE, queue, "# Sprint queue — managed by lisa_sync.py.\n# Ordered by dashboard_impact (estimated $/month, highest first).\n# First item = UNACKNOWLEDGED on the dashboard.\n\n")
    save_yaml(ACTIVE, active, "# Active experiments — current_reading updated by lisa_sync.py.\n# State transitions managed by sprint_action.py and evaluate_experiment.py.\n\n")
    save_yaml(SCANS, scans, "# Weekly scan state — updated by lisa_sync.py.\n\n")
    append_log({"run_id": f"scan-{now.strftime('%Y%m%d-%H%M%S')}", "type": "daily_scan",
                "status": "success", "duration_ms": int((time.time() - t0) * 1000),
                "pages_scanned": pages_scanned,
                "sources": ["Google Search Console", "Google Analytics 4", "Shopify Admin API"],
                "summary": summary, "ts": now_iso})
    return 0


if __name__ == "__main__":
    sys.exit(main())
