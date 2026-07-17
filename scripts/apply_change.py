#!/usr/bin/env python3
"""
apply_change.py — apply a sprint's proposed change to Shopify directly.

Reads the sprint's execution_brief.apply payload from sprints/queue.yml and
writes the change via the Shopify Admin API. Currently supports SEO metadata
(title_tag / description_tag) on blog articles and products.

    python scripts/apply_change.py 149

Called by the sprint-action workflow when the dashboard's ACCEPT button is
clicked (action: "apply"); the workflow then runs `sprint_action.py done` so
the sprint starts tracking as an experiment.

Requires SHOPIFY_STORE_DOMAIN / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET in
the environment (or .env.local), and the app needs write scopes:
`write_content` for articles/pages, `write_products` for products.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE = REPO_ROOT / "sprints" / "queue.yml"
LOGS = REPO_ROOT / "logs" / "runs.jsonl"
API = "2025-04"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lisa_sync import load_env  # noqa: E402


def shopify_headers(env: dict) -> dict:
    r = requests.post(
        f"https://{env['SHOPIFY_STORE_DOMAIN']}/admin/oauth/access_token",
        json={"grant_type": "client_credentials",
              "client_id": env["SHOPIFY_CLIENT_ID"],
              "client_secret": env["SHOPIFY_CLIENT_SECRET"]},
        timeout=30,
    )
    r.raise_for_status()
    return {"X-Shopify-Access-Token": r.json()["access_token"],
            "Content-Type": "application/json"}


def seo_metafields(set_: dict) -> list[dict]:
    fields = []
    if set_.get("title_tag"):
        fields.append({"namespace": "global", "key": "title_tag",
                       "value": set_["title_tag"], "type": "single_line_text_field"})
    if set_.get("description_tag"):
        fields.append({"namespace": "global", "key": "description_tag",
                       "value": set_["description_tag"], "type": "multi_line_text_field"})
    return fields


def apply_article(env: dict, headers: dict, apply: dict) -> str:
    domain = env["SHOPIFY_STORE_DOMAIN"]
    blogs = requests.get(f"https://{domain}/admin/api/{API}/blogs.json?limit=50",
                         headers=headers, timeout=30)
    blogs.raise_for_status()
    blog = next((b for b in blogs.json()["blogs"] if b["handle"] == apply["blog_handle"]), None)
    if not blog:
        raise SystemExit(f"blog handle '{apply['blog_handle']}' not found")
    arts = requests.get(
        f"https://{domain}/admin/api/{API}/blogs/{blog['id']}/articles.json"
        f"?handle={apply['article_handle']}&limit=1",
        headers=headers, timeout=30)
    arts.raise_for_status()
    articles = arts.json().get("articles", [])
    if not articles:
        raise SystemExit(f"article handle '{apply['article_handle']}' not found in blog '{apply['blog_handle']}'")
    art_id = articles[0]["id"]
    resp = requests.put(
        f"https://{domain}/admin/api/{API}/blogs/{blog['id']}/articles/{art_id}.json",
        headers=headers,
        json={"article": {"id": art_id, "metafields": seo_metafields(apply["set"])}},
        timeout=30)
    if resp.status_code == 403:
        raise SystemExit("403 from Shopify — the app is missing the write_content scope. "
                         "Add it in the app configuration, reinstall, and retry.")
    resp.raise_for_status()
    return f"article {apply['blog_handle']}/{apply['article_handle']} (id {art_id})"


def apply_product(env: dict, headers: dict, apply: dict) -> str:
    domain = env["SHOPIFY_STORE_DOMAIN"]
    prods = requests.get(
        f"https://{domain}/admin/api/{API}/products.json?handle={apply['handle']}&limit=1&fields=id,handle",
        headers=headers, timeout=30)
    prods.raise_for_status()
    products = prods.json().get("products", [])
    if not products:
        raise SystemExit(f"product handle '{apply['handle']}' not found")
    pid = products[0]["id"]
    body = {"product": {"id": pid}}
    if apply["set"].get("title_tag"):
        body["product"]["metafields_global_title_tag"] = apply["set"]["title_tag"]
    if apply["set"].get("description_tag"):
        body["product"]["metafields_global_description_tag"] = apply["set"]["description_tag"]
    resp = requests.put(f"https://{domain}/admin/api/{API}/products/{pid}.json",
                        headers=headers, json=body, timeout=30)
    if resp.status_code == 403:
        raise SystemExit("403 from Shopify — the app is missing the write_products scope. "
                         "Add it in the app configuration, reinstall, and retry.")
    resp.raise_for_status()
    return f"product {apply['handle']} (id {pid})"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: apply_change.py <sprint_id>", file=sys.stderr)
        return 2
    sprint_id = sys.argv[1]

    queue = yaml.safe_load(QUEUE.open()) or {}
    sprint = next((s for s in queue.get("sprints", []) if s.get("id") == sprint_id), None)
    if not sprint:
        print(f"sprint {sprint_id} not found in queue", file=sys.stderr)
        return 1
    apply = (sprint.get("execution_brief") or {}).get("apply")
    if not apply:
        print(f"sprint {sprint_id} has no execution_brief.apply payload — nothing to auto-apply", file=sys.stderr)
        return 1

    env = load_env()
    headers = shopify_headers(env)
    resource = apply.get("resource")
    if resource == "article":
        target = apply_article(env, headers, apply)
    elif resource == "product":
        target = apply_product(env, headers, apply)
    else:
        print(f"unsupported apply resource: {resource}", file=sys.stderr)
        return 1

    with LOGS.open("a") as f:
        f.write(json.dumps({
            "run_id": f"apply-{sprint_id}",
            "type": "manual_action",
            "status": "success",
            "sprint_id": sprint_id,
            "summary": f"Sprint #{sprint_id}: applied SEO change to {target} via Admin API.",
            "changes": apply["set"],
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }) + "\n")
    print(f"✓ applied sprint {sprint_id} change to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
