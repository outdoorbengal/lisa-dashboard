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


def fetch_shopify(env: dict, start: date, end: date) -> dict:
    """Mint a client-credentials token; pull order economics for the window.
    Returns aov, window revenue/order count (for GA4 reconciliation) and the
    share of single-line orders (bundle/cross-sell headroom signal)."""
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
    orders, url = [], (f"https://{domain}/admin/api/2025-04/orders.json"
                      f"?status=any&created_at_min={start.isoformat()}T00:00:00Z"
                      f"&created_at_max={end.isoformat()}T23:59:59Z"
                      f"&limit=250&fields=total_price,line_items,financial_status,source_name")
    for _ in range(8):  # up to 2000 orders via cursor pagination
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        orders.extend(r.json().get("orders", []))
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
        if not nxt:
            break
        url = nxt.group(1)
    paid = [o for o in orders if o.get("financial_status") not in ("voided", "refunded")]
    # GA4 only ever sees online-store checkouts — reconcile against those,
    # not drafts / POS / marketplace orders.
    web = [o for o in paid if o.get("source_name") == "web"]
    totals = [float(o["total_price"]) for o in paid]
    web_totals = [float(o["total_price"]) for o in web]
    single = sum(1 for o in paid if sum(int(li.get("quantity", 1)) for li in o.get("line_items", [])) == 1)
    by_source = {}
    for o in paid:
        by_source[o.get("source_name") or "unknown"] = by_source.get(o.get("source_name") or "unknown", 0) + 1
    count = requests.get(f"https://{domain}/admin/api/2025-04/products/count.json",
                         headers=headers, timeout=30)
    return {
        "aov": round(sum(web_totals) / len(web_totals), 2) if web_totals else
               (round(sum(totals) / len(totals), 2) if totals else FALLBACK_AOV),
        "revenue": round(sum(totals), 2),
        "web_revenue": round(sum(web_totals), 2),
        "orders": len(paid),
        "web_orders": len(web),
        "orders_by_source": by_source,
        "single_item_share": round(single / len(paid), 3) if paid else None,
        "products": count.json().get("count", 0) if count.status_code == 200 else 0,
    }


def fetch_ga4_events(env: dict, headers: dict, start: date, end: date) -> dict:
    """Sitewide funnel event counts: {event_name: count}."""
    r = requests.post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{env['GA4_PROPERTY_ID']}:runReport",
        headers=headers,
        json={"dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
              "dimensions": [{"name": "eventName"}],
              "metrics": [{"name": "eventCount"}],
              "dimensionFilter": {"filter": {"fieldName": "eventName", "inListFilter": {
                  "values": ["view_item", "add_to_cart", "begin_checkout", "purchase"]}}}},
        timeout=60,
    )
    r.raise_for_status()
    out = {k: 0 for k in ("view_item", "add_to_cart", "begin_checkout", "purchase")}
    for row in r.json().get("rows", []):
        out[row["dimensionValues"][0]["value"]] = int(float(row["metricValues"][0]["value"]))
    return out


def fetch_ga4_devices(env: dict, headers: dict, start: date, end: date) -> dict:
    """{device: {sessions, purchases, revenue}} for mobile/desktop/tablet."""
    r = requests.post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{env['GA4_PROPERTY_ID']}:runReport",
        headers=headers,
        json={"dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
              "dimensions": [{"name": "deviceCategory"}],
              "metrics": [{"name": "sessions"}, {"name": "ecommercePurchases"}, {"name": "purchaseRevenue"}]},
        timeout=60,
    )
    r.raise_for_status()
    out = {}
    for row in r.json().get("rows", []):
        vals = [float(m["value"] or 0) for m in row["metricValues"]]
        out[row["dimensionValues"][0]["value"]] = {"sessions": vals[0], "purchases": vals[1], "revenue": vals[2]}
    return out


def fetch_gsc_query_pages(env: dict, headers: dict, start: date, end: date) -> list:
    """[(query, path, clicks, impressions, position)] for cannibalization checks."""
    site = quote(env["GSC_SITE_URL"], safe="")
    r = requests.post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query",
        headers=headers,
        json={"startDate": start.isoformat(), "endDate": end.isoformat(),
              "dimensions": ["query", "page"], "rowLimit": 25000, "dataState": "final"},
        timeout=60,
    )
    r.raise_for_status()
    return [(row["keys"][0], norm_path(row["keys"][1]), row["clicks"], row["impressions"], row["position"])
            for row in r.json().get("rows", [])]


# ── measurement (shared by queue refresh and experiment readings) ────
def measure(metric: str, path: str, gsc: dict, ga4: dict, ga4_totals: dict, extra: dict | None = None):
    """Returns (value, display, note) or None if this metric isn't measurable."""
    g = gsc.get(path)
    a = ga4.get(path)
    extra = extra or {}
    devices, events = extra.get("devices") or {}, extra.get("events") or {}
    if metric == "mobile_conversion_rate":
        m = devices.get("mobile")
        if not m or m["sessions"] < 100:
            return None
        v = round(m["purchases"] / m["sessions"] * 100, 2)
        return v, f"{v:g}% now", f"{int(m['purchases'])} purchases / {int(m['sessions'])} mobile sessions ({WINDOW_DAYS}d)"
    if metric in ("atc_rate", "checkout_rate", "purchase_completion_rate"):
        num_k, den_k = {"atc_rate": ("add_to_cart", "view_item"),
                        "checkout_rate": ("begin_checkout", "add_to_cart"),
                        "purchase_completion_rate": ("purchase", "begin_checkout")}[metric]
        if not events.get(den_k):
            return None
        v = round(events[num_k] / events[den_k] * 100, 1)
        return v, f"{v:g}% now", f"{events[num_k]} {num_k} / {events[den_k]} {den_k} ({WINDOW_DAYS}d)"
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


# ── checks engine ────────────────────────────────────────────────────
# Each check inspects the pulled data and returns "finding" dicts:
#   {type, url, title, kpi, effort, rationale, evaluation,
#    revenue_model, impact}
# main() dedupes findings against queue+active+archive on
# (url, type, kpi.metric), keeps the top MAX_NEW_PER_RUN by impact and
# wraps them into queue sprints. Diagnostics (tracking health) are
# separate: they produce warnings, not revenue-ranked sprints.

FUNNEL_BENCHMARKS = {  # conservative DTC medians: (numerator, denominator, benchmark rate)
    "atc_rate": ("add_to_cart", "view_item", 0.07),
    "checkout_rate": ("begin_checkout", "add_to_cart", 0.35),
    "purchase_completion_rate": ("purchase", "begin_checkout", 0.45),
}
BRAND_TOKENS = ("outdoorbengal", "outdoor bengal")


def _slug(path: str) -> str:
    return path.rstrip("/").split("/")[-1] or path


def check_page_ctr(ctx) -> list:
    out = []
    for path, g in ctx["gsc"].items():
        if not is_scannable(path):
            continue
        if g["impressions"] >= 1000 and g["position"] <= 12 and g["ctr"] < 0.5 * expected_ctr(g["position"]):
            impact = score_impact("CTR", path, ctx["gsc"], ctx["ga4"], ctx["ga4_totals"], ctx["aov"])
            if not impact:
                continue
            v, tgt = round(g["ctr"] * 100, 2), round(expected_ctr(g["position"]) * 100, 2)
            q = ctx["top_query"].get(path, "")
            out.append({
                "type": "CTR", "url": path, "impact": impact,
                "title": f"Fix title/meta — {_slug(path)} ({int(g['impressions'])} impressions)",
                "kpi": {"metric": "ctr", "display_label": f"CTR · {int(g['impressions'])} impressions · pos {g['position']:.1f}",
                        "current_value": v, "current_display": f"{v:g}% now",
                        "target_value": tgt, "target_display": f"{tgt:g}%+", "unit": "percent"},
                "effort": {"level": "LOW", "description": "Title + meta rewrite only", "estimated_minutes": 20},
                "rationale": f"Ranking pos {g['position']:.1f}" + (f" for '{q}'" if q else "")
                             + f" but CTR is {v:g}% vs ~{tgt:g}% expected — title/meta underselling the click",
                "evaluation": {"window_days": 42, "success_threshold_pct": 15, "neutral_threshold_pct": 0},
                "revenue_model": build_revenue_model("CTR", path, ctx["gsc"], ctx["ga4"], ctx["ga4_totals"], ctx["aov"]),
            })
    return out


def check_page_rank(ctx) -> list:
    out = []
    for path, g in ctx["gsc"].items():
        if not is_scannable(path):
            continue
        if 8 <= g["position"] <= 20 and g["impressions"] >= 500:
            impact = score_impact("RNK", path, ctx["gsc"], ctx["ga4"], ctx["ga4_totals"], ctx["aov"])
            if not impact:
                continue
            wk = round(g["clicks"] / (WINDOW_DAYS / 7), 1)
            q = ctx["top_query"].get(path, "")
            out.append({
                "type": "RNK", "url": path, "impact": impact,
                "title": f"Push to page 1 — {_slug(path)}",
                "kpi": {"metric": "organic_clicks_weekly", "display_label": f"Weekly clicks · pos {g['position']:.1f}",
                        "current_value": wk, "current_display": f"~{wk:g}/wk now",
                        "target_value": round(max(wk * 3, wk + 4), 1),
                        "target_display": f"+{round(max(wk * 2, 4), 1):g}/wk", "unit": "sessions/week"},
                "effort": {"level": "MEDIUM", "description": "Copy + internal links", "estimated_minutes": 45},
                "rationale": f"Ranking pos {g['position']:.1f}" + (f" for '{q}'" if q else "")
                             + f" — {int(g['impressions'])} impressions stuck on page 2",
                "evaluation": {"window_days": 42, "success_threshold_pct": 15, "neutral_threshold_pct": 0},
                "revenue_model": build_revenue_model("RNK", path, ctx["gsc"], ctx["ga4"], ctx["ga4_totals"], ctx["aov"]),
            })
    return out


def check_page_cr(ctx) -> list:
    out = []
    for path, a in ctx["ga4"].items():
        if not is_scannable(path) or not path.startswith("/products/"):
            continue
        if a["sessions"] >= 300 and (a["purchases"] / a["sessions"]) < 0.01:
            impact = score_impact("CR", path, ctx["gsc"], ctx["ga4"], ctx["ga4_totals"], ctx["aov"])
            if not impact:
                continue
            cr = a["purchases"] / a["sessions"]
            out.append({
                "type": "CR", "url": path, "impact": impact,
                "title": f"Conversion fix — {_slug(path)}",
                "kpi": {"metric": "conversion_rate", "display_label": f"Conversion rate · {int(a['sessions'])} sessions",
                        "current_value": round(cr * 100, 2), "current_display": f"{cr * 100:.2f}% now",
                        "target_value": round(max(cr * 1.5, 0.01) * 100, 2),
                        "target_display": f"{max(cr * 1.5, 0.01) * 100:.2f}%", "unit": "percent"},
                "effort": {"level": "MEDIUM", "description": "PDP copy / offer / trust review", "estimated_minutes": 60},
                "rationale": f"{int(a['sessions'])} sessions/{WINDOW_DAYS}d but only {cr * 100:.2f}% conversion — revenue leak",
                "evaluation": {"window_days": 28, "success_threshold_pct": 25, "neutral_threshold_pct": 0},
                "revenue_model": build_revenue_model("CR", path, ctx["gsc"], ctx["ga4"], ctx["ga4_totals"], ctx["aov"]),
            })
    return out


def check_declining_pages(ctx) -> list:
    """Pages losing organic clicks vs the previous window → refresh candidates."""
    out, month = [], 30 / WINDOW_DAYS
    for path, prev in ctx["gsc_prev"].items():
        if not is_scannable(path) or prev["clicks"] < 100:
            continue
        cur = ctx["gsc"].get(path, {"clicks": 0.0, "impressions": 0.0, "ctr": 0.0, "position": 99.0})
        drop = (prev["clicks"] - cur["clicks"]) / prev["clicks"]
        if drop < 0.30:
            continue
        cr = page_cr(path, ctx["ga4"], ctx["ga4_totals"])
        impact = round((prev["clicks"] - cur["clicks"]) * month * cr * ctx["aov"], 2)
        if impact < MIN_IMPACT_USD:
            continue
        wk = round(cur["clicks"] / (WINDOW_DAYS / 7), 1)
        rm = None
        if cur["impressions"]:
            rm = {"current": {"impressions_30d": int(cur["impressions"] * month), "ctr": round(cur["ctr"], 4),
                              "sessions_30d": int(cur["clicks"] * month), "conversion_rate": round(cr, 4),
                              "aov": round(ctx["aov"], 2)},
                  "projected": {"impressions_multiplier": round(min(prev["clicks"] / max(cur["clicks"], 1), 2.0), 2)},
                  "confidence": "low"}
        out.append({
            "type": "DEC", "url": path, "impact": impact,
            "title": f"Refresh declining content — {_slug(path)} (-{drop * 100:.0f}% clicks)",
            "kpi": {"metric": "organic_clicks_weekly", "display_label": f"Weekly clicks · was {round(prev['clicks'] / 4):g}/wk",
                    "current_value": wk, "current_display": f"~{wk:g}/wk now",
                    "target_value": round(prev["clicks"] / 4, 1), "target_display": f"{round(prev['clicks'] / 4):g}/wk",
                    "unit": "sessions/week"},
            "effort": {"level": "MEDIUM", "description": "Update facts, intent match, media, internal links", "estimated_minutes": 60},
            "rationale": (f"Organic clicks fell {drop * 100:.0f}% vs previous {WINDOW_DAYS}d "
                          f"({int(prev['clicks'])} → {int(cur['clicks'])}); position {prev['position']:.1f} → {cur['position']:.1f}. "
                          f"Likely content decay or SERP shift — refresh and re-align to intent."),
            "evaluation": {"window_days": 42, "success_threshold_pct": 20, "neutral_threshold_pct": 0},
            "revenue_model": rm,
        })
    return out


def check_cannibalization(ctx) -> list:
    """Two+ pages splitting impressions for one query → consolidate."""
    by_query = {}
    for query, path, clicks, imp, pos in ctx["query_pages"]:
        if any(t in query.lower() for t in BRAND_TOKENS) or not is_scannable(path):
            continue
        by_query.setdefault(query, []).append((path, clicks, imp, pos))
    out, month = [], 30 / WINDOW_DAYS
    for query, rows in by_query.items():
        total_imp = sum(r[2] for r in rows)
        if total_imp < 1000 or len(rows) < 2:
            continue
        splitters = sorted((r for r in rows if r[2] / total_imp >= 0.25), key=lambda r: -r[2])
        if len(splitters) < 2:
            continue
        top = splitters[0]
        cr = page_cr(top[0], ctx["ga4"], ctx["ga4_totals"])
        q_ctr = sum(r[1] for r in rows) / total_imp
        gain = max(expected_ctr(top[3]) - q_ctr, 0) * 0.5  # 50% consolidation credit
        impact = round(total_imp * month * gain * cr * ctx["aov"], 2)
        if impact < MIN_IMPACT_USD:
            continue
        wk = round(top[1] / (WINDOW_DAYS / 7), 1)
        competing = ", ".join(p for p, *_ in splitters[1:3])
        out.append({
            "type": "RNK", "url": top[0], "impact": impact,
            "title": f"Fix cannibalization — '{query[:40]}'",
            "kpi": {"metric": "organic_clicks_weekly", "display_label": f"Weekly clicks · {len(splitters)} pages splitting '{query[:30]}'",
                    "current_value": wk, "current_display": f"~{wk:g}/wk now",
                    "target_value": round(max(wk * 2, wk + 3), 1), "target_display": f"+{round(max(wk, 3), 1):g}/wk",
                    "unit": "sessions/week"},
            "effort": {"level": "MEDIUM", "description": "Consolidate/differentiate competing pages, canonical or redirect", "estimated_minutes": 45},
            "rationale": (f"{len(splitters)} pages split {int(total_imp)} impressions for '{query}' "
                          f"(best pos {top[3]:.1f}). Competing: {competing}. Consolidate intent onto {top[0]}, "
                          f"differentiate or redirect the rest."),
            "evaluation": {"window_days": 42, "success_threshold_pct": 15, "neutral_threshold_pct": 0},
            "revenue_model": {"current": {"impressions_30d": int(total_imp * month), "ctr": round(q_ctr, 4),
                                          "sessions_30d": int(sum(r[1] for r in rows) * month),
                                          "conversion_rate": round(cr, 4), "aov": round(ctx["aov"], 2)},
                              "projected": {"ctr": round(q_ctr + gain, 4)}, "confidence": "low"},
        })
    out.sort(key=lambda f: -f["impact"])
    return out[:2]  # at most two cannibalization findings per run


def check_mobile_gap(ctx) -> list:
    d = ctx["devices"]
    mob, desk = d.get("mobile"), d.get("desktop")
    if not mob or not desk or mob["sessions"] < 1000 or desk["purchases"] < 10:
        return []
    mob_cr, desk_cr = mob["purchases"] / mob["sessions"], desk["purchases"] / desk["sessions"]
    if desk_cr <= 0 or mob_cr >= 0.6 * desk_cr:
        return []
    month = 30 / WINDOW_DAYS
    target = 0.75 * desk_cr
    impact = round(mob["sessions"] * month * (target - mob_cr) * ctx["aov"], 2)
    if impact < MIN_IMPACT_USD:
        return []
    return [{
        "type": "FUN", "url": "/", "impact": impact,
        "title": f"Close the mobile conversion gap ({mob_cr * 100:.2f}% vs {desk_cr * 100:.2f}% desktop)",
        "kpi": {"metric": "mobile_conversion_rate", "display_label": f"Mobile CR · {int(mob['sessions'])} sessions",
                "current_value": round(mob_cr * 100, 2), "current_display": f"{mob_cr * 100:.2f}% now",
                "target_value": round(target * 100, 2), "target_display": f"{target * 100:.2f}%", "unit": "percent"},
        "effort": {"level": "HIGH", "description": "Mobile PDP/cart/checkout UX audit + fixes", "estimated_minutes": 120},
        "rationale": (f"Mobile converts at {mob_cr * 100:.2f}% vs {desk_cr * 100:.2f}% on desktop "
                      f"({int(mob['sessions'])} vs {int(desk['sessions'])} sessions/{WINDOW_DAYS}d). "
                      f"Audit mobile PDP hierarchy, sticky add-to-cart, tap targets, pop-up interference, checkout usability."),
        "evaluation": {"window_days": 28, "success_threshold_pct": 15, "neutral_threshold_pct": 0},
        "revenue_model": {"current": {"impressions_30d": 0, "ctr": 0.0,
                                      "sessions_30d": int(mob["sessions"] * month),
                                      "conversion_rate": round(mob_cr, 4), "aov": round(ctx["aov"], 2)},
                          "projected": {"conversion_rate": round(target, 4)}, "confidence": "medium"},
    }]


def check_funnel_stage(ctx) -> list:
    """Flag the single worst funnel stage vs benchmark."""
    ev = ctx["events"]
    if ev.get("purchase", 0) < 20:
        return []
    worst = None
    for metric, (num, den, bench) in FUNNEL_BENCHMARKS.items():
        if not ev.get(den):
            continue
        rate = ev[num] / ev[den]
        ratio = rate / bench
        if ratio < 0.6 and (worst is None or ratio < worst[3]):
            worst = (metric, num, den, ratio, rate, bench)
    if not worst:
        return []
    metric, num, den, ratio, rate, bench = worst
    month = 30 / WINDOW_DAYS
    site = ctx["ga4_totals"]
    site_cr = site["purchases"] / site["sessions"] if site["sessions"] else 0.01
    lift = min(bench / rate - 1, 0.5)  # cap claimed upside at +50%
    impact = round(site["revenue"] * month * lift * 0.5, 2)  # 50% attainability haircut
    if impact < MIN_IMPACT_USD:
        return []
    stage_names = {"atc_rate": "product view → add-to-cart",
                   "checkout_rate": "add-to-cart → checkout",
                   "purchase_completion_rate": "checkout → purchase"}
    return [{
        "type": "FUN", "url": "/", "impact": impact,
        "title": f"Fix funnel stage: {stage_names[metric]} ({rate * 100:.1f}% vs ~{bench * 100:.0f}% benchmark)",
        "kpi": {"metric": metric, "display_label": f"{stage_names[metric]} · {ev[den]} {den}/{WINDOW_DAYS}d",
                "current_value": round(rate * 100, 1), "current_display": f"{rate * 100:.1f}% now",
                "target_value": round(bench * 100, 1), "target_display": f"{bench * 100:.0f}%", "unit": "percent"},
        "effort": {"level": "HIGH", "description": "Diagnose stage drop-off, fix top friction", "estimated_minutes": 120},
        "rationale": (f"{stage_names[metric]} converts at {rate * 100:.1f}% ({ev[num]}/{ev[den]} over {WINDOW_DAYS}d) "
                      f"vs a ~{bench * 100:.0f}% benchmark — the weakest stage of the funnel. "
                      f"Investigate with device split, error logs and session recordings before changing UI."),
        "evaluation": {"window_days": 28, "success_threshold_pct": 15, "neutral_threshold_pct": 0},
        "revenue_model": {"current": {"impressions_30d": 0, "ctr": 0.0,
                                      "sessions_30d": int(site["sessions"] * month),
                                      "conversion_rate": round(site_cr, 4), "aov": round(ctx["aov"], 2)},
                          "projected": {"conversion_rate": round(site_cr * (1 + lift * 0.5), 4)}, "confidence": "low"},
    }]


CHECKS = [check_page_ctr, check_page_rank, check_page_cr,
          check_declining_pages, check_cannibalization,
          check_mobile_gap, check_funnel_stage]


def run_diagnostics(ctx) -> list[str]:
    """Phase-1 tracking health. Warnings, not sprints — an optimizer should
    not rank revenue plays on top of analytics it hasn't validated."""
    warnings = []
    ga4_rev = sum(v["revenue"] for v in ctx["devices"].values())
    ga4_purch = sum(v["purchases"] for v in ctx["devices"].values())
    shop = ctx["shop"]
    if shop["web_revenue"] > 0:
        diff = abs(ga4_rev - shop["web_revenue"]) / shop["web_revenue"]
        if diff > 0.15:
            warnings.append(f"Revenue mismatch: GA4 ${ga4_rev:,.0f} vs Shopify online-store ${shop['web_revenue']:,.0f} "
                            f"({diff * 100:.0f}% gap, {WINDOW_DAYS}d) — check purchase tracking, refunds, payment referral exclusions")
    if shop["web_orders"] > 0:
        diff = abs(ga4_purch - shop["web_orders"]) / shop["web_orders"]
        if diff > 0.15:
            warnings.append(f"Order-count mismatch: GA4 {int(ga4_purch)} purchases vs Shopify {shop['web_orders']} online-store orders "
                            f"({diff * 100:.0f}% gap) — duplicate or missing purchase events")
    for name, count in ctx["events"].items():
        if count == 0:
            warnings.append(f"Funnel event '{name}' fired 0 times in {WINDOW_DAYS}d — tracking broken or not installed")
    if shop.get("single_item_share") is not None and shop["single_item_share"] >= 0.70:
        warnings.append(f"{shop['single_item_share'] * 100:.0f}% of orders contain a single item — "
                        f"bundle/cross-sell headroom (AOV ${ctx['aov']:g})")
    return warnings


def finding_to_sprint(next_id: int, f: dict, now_iso: str) -> dict:
    opportunity_type = {"CTR": "SEARCH_VISIBILITY", "RNK": "TRAFFIC_GROWTH", "CR": "CONVERSION_OPTIMIZATION",
                        "DEC": "CONTENT_DECAY", "FUN": "FUNNEL_OPTIMIZATION"}.get(f["type"], "OPPORTUNITY")
    return {
        "id": f"{next_id:03d}",
        "created": date.today().isoformat(),
        "title": f["title"],
        "url": f["url"],
        "sprint_type": f["type"],
        "kpi": f["kpi"],
        "effort": f["effort"],
        "rationale": f"{f['rationale']}\n\nDashboard impact: ${f['impact']:g}/mo estimated",
        "evaluation": f["evaluation"],
        "evidence_urls": [],
        "_source": {"opportunity_type": opportunity_type, "detected_at": now_iso,
                    "last_refreshed": now_iso, "dashboard_impact": f["impact"]},
        "revenue_model": f["revenue_model"],
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
    prev_end = start - timedelta(days=1)
    gsc_prev = fetch_gsc(env, gheaders, prev_end - timedelta(days=WINDOW_DAYS - 1), prev_end)
    query_pages = fetch_gsc_query_pages(env, gheaders, start, end)
    ga4, ga4_totals = fetch_ga4(env, gheaders, start, end)
    events = fetch_ga4_events(env, gheaders, start, end)
    devices = fetch_ga4_devices(env, gheaders, start, end)
    shop = fetch_shopify(env, start, end)
    aov = shop["aov"]
    pages_scanned = len(set(gsc) | set(ga4))
    print(f"Pulled: {len(gsc)} GSC pages, {len(ga4)} GA4 landing pages, "
          f"{len(query_pages)} query-page rows, {shop['orders']} orders, AOV ${aov:g}")

    # Top query per page (for rationale text), derived from the query-page pull
    top_query = {}
    for q, path, clicks, imp, pos in query_pages:
        if imp > top_query.get(path, ("", 0))[1]:
            top_query[path] = (q, imp)
    top_query = {p: q for p, (q, _) in top_query.items()}

    ctx = {"gsc": gsc, "gsc_prev": gsc_prev, "query_pages": query_pages,
           "ga4": ga4, "ga4_totals": ga4_totals, "events": events,
           "devices": devices, "shop": shop, "aov": aov, "top_query": top_query}
    extra = {"events": events, "devices": devices}

    # 0. Diagnostics first — never optimize on top of broken measurement
    warnings = run_diagnostics(ctx)
    for w in warnings:
        print(f"  ⚠ {w}")

    queue = load_yaml(QUEUE)
    active = load_yaml(ACTIVE)
    archive = load_yaml(ARCHIVE)
    scans = load_yaml(SCANS)

    # 1. Refresh queue KPIs + scores
    reranked = 0
    for sp in queue.get("sprints", []):
        path = norm_path(sp.get("url", ""))
        m = measure(sp["kpi"].get("metric", ""), path, gsc, ga4, ga4_totals, extra)
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

    # 2. Run all checks, dedupe, keep the top findings
    def key_of(item):
        return (norm_path(item.get("url", "")), item.get("sprint_type"), (item.get("kpi") or {}).get("metric"))
    taken = {key_of(s) for s in queue.get("sprints", [])}
    taken |= {key_of(e) for e in active.get("experiments", [])}
    taken |= {key_of(e) for e in archive.get("experiments", [])}
    findings = []
    for check in CHECKS:
        try:
            findings.extend(check(ctx))
        except Exception as e:
            warnings.append(f"check {check.__name__} crashed: {e}")
    findings = [f for f in findings
                if (norm_path(f["url"]), f["type"], f["kpi"]["metric"]) not in taken
                and f["impact"] >= MIN_IMPACT_USD]
    findings.sort(key=lambda f: -f["impact"])
    next_id = max(all_ids((queue, "sprints"), (active, "experiments"), (archive, "experiments")), default=0) + 1
    new_sprints = []
    for f in findings[:MAX_NEW_PER_RUN]:
        new_sprints.append(finding_to_sprint(next_id, f, now_iso))
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
        m = measure(exp.get("kpi", {}).get("metric", ""), path, gsc, ga4, ga4_totals, extra)
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
               f"{reranked} reranked. {measured} active experiments re-measured."
               + (f" {len(warnings)} tracking warnings." if warnings else ""))
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
                "status": "warning" if warnings else "success",
                "duration_ms": int((time.time() - t0) * 1000),
                "pages_scanned": pages_scanned,
                "sources": ["Google Search Console", "Google Analytics 4", "Shopify Admin API"],
                "summary": summary, **({"warnings": warnings} if warnings else {}),
                "ts": now_iso})
    return 0


if __name__ == "__main__":
    sys.exit(main())
