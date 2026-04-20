# Setup guide

Step-by-step deployment. Plan on ~30 minutes the first time.

## Prerequisites

- A GitHub account that owns (or can admin) the `outdoorbengal/lisa-dashboard` repo
- A Cloudflare account (free tier is fine — we deploy one Worker)
- Node.js installed locally (for the Worker deployment only)

---

## 1. Create the repo and push this code

```bash
# From the directory containing this project:
git init
git add .
git commit -m "initial: Lisa dashboard v2"
git branch -M main
git remote add origin https://github.com/outdoorbengal/lisa-dashboard.git
git push -u origin main
```

---

## 2. Enable GitHub Pages

1. Go to **Settings → Pages** in your repo.
2. Under "Build and deployment", set **Source** to **GitHub Actions**.
3. That's it — no branch selection needed. The `build-dashboard.yml`
   workflow handles deployment.

Once the workflow runs, your dashboard will be live at:
`https://outdoorbengal.github.io/lisa-dashboard/`

---

## 3. Register a GitHub OAuth App

1. Go to https://github.com/settings/developers
2. Click **OAuth Apps → New OAuth App**
3. Fill in:
   - **Application name:** Lisa Dashboard
   - **Homepage URL:** `https://outdoorbengal.github.io/lisa-dashboard`
   - **Authorization callback URL:** `https://outdoorbengal.github.io/lisa-dashboard/`
     (trailing slash matters — must exactly match)
4. Click **Register application**
5. On the next page, copy the **Client ID** — you'll paste it into
   `public/config.js` in step 5.
6. Click **Generate a new client secret**, copy the value — you'll paste
   it into the Cloudflare Worker in step 4.

---

## 4. Deploy the Cloudflare Worker (token exchange)

The Worker exists only so your OAuth client_secret never ships to the
browser. It has one endpoint: `POST /exchange`, which takes a `code` and
returns an `access_token`.

```bash
# Anywhere outside your dashboard repo:
npm create cloudflare@latest lisa-oauth
# Choose: "Hello World" Worker → TypeScript or JavaScript (either works)
# Don't deploy yet when prompted — we need to replace the source first.

cd lisa-oauth
# Replace src/index.js (or src/index.ts) with the contents of
# docs/oauth-worker.js from the dashboard repo.

# Set secrets:
npx wrangler secret put GITHUB_CLIENT_ID
# (paste the Client ID from step 3)
npx wrangler secret put GITHUB_CLIENT_SECRET
# (paste the Client Secret from step 3)
npx wrangler secret put ALLOWED_ORIGIN
# Paste: https://outdoorbengal.github.io

# Deploy:
npx wrangler deploy
```

Wrangler will print a URL like `https://lisa-oauth.YOUR-SUBDOMAIN.workers.dev`.
Copy it — you'll paste it into `public/config.js` next.

---

## 5. Configure the dashboard

Edit `public/config.js` in your dashboard repo:

```js
window.LISA_CONFIG = {
  repo: "outdoorbengal/lisa-dashboard",
  oauthClientId: "Iv1.abc123def456",  // ← from step 3
  tokenExchangeUrl: "https://lisa-oauth.YOUR-SUBDOMAIN.workers.dev/exchange",  // ← from step 4
  allowedUsers: [],  // leave empty for solo use, or ["outdoorbengal"] to restrict
};
```

Commit and push:

```bash
git add public/config.js
git commit -m "config: wire up OAuth"
git push
```

The build workflow will redeploy.

---

## 6. First login

1. Visit `https://outdoorbengal.github.io/lisa-dashboard/`
2. Click **Sign in with GitHub**
3. Approve the OAuth app (one-time; GitHub remembers your approval)
4. You'll land on the dashboard, authenticated

Your session persists via `localStorage` — you won't need to log in
again on this device unless you click **Sign out**, clear browser data,
or the token expires (8 hours by default).

**If login fails:** check the browser console. The most common issues are:

- Callback URL mismatch — must be exactly `https://outdoorbengal.github.io/lisa-dashboard/` (with trailing slash)
- Worker `ALLOWED_ORIGIN` mismatch — must be `https://outdoorbengal.github.io` (no trailing slash, no path)
- Worker secrets not set — run `npx wrangler secret list` to verify

---

## 7. Test a button click

1. On the dashboard, click **DONE** on a pending sprint.
2. Confirm in the dialog.
3. Watch the Actions tab on GitHub — you should see `Sprint Action` run,
   followed by `Build & Deploy Dashboard`.
4. After ~30s the dashboard reloads and shows the sprint moved from
   Sprints to Experiments.

If the dispatch fails, the toast will tell you. Most common issue: your
token doesn't have `repo` scope, which means the OAuth app registration
is missing that scope in step 3. Sign out, revoke the app at
https://github.com/settings/applications, and sign in again.

---

## 8. Point Lisa/Marge at the new source files

Update your orchestrator's instructions per `docs/LISA_SPEC.md`. The key
change: Lisa now edits `sprints/queue.yml`, `scans/state.yml`, and
`logs/runs.jsonl` via normal git commits. She never touches `data.json`,
never holds a GitHub token, and never uses the GitHub Contents API to
PUT files.

---

## Troubleshooting cheat sheet

| Symptom | Likely cause |
|---|---|
| Dashboard stuck on "Loading" | `data.json` missing or malformed; check Actions tab |
| Login redirects but never completes | Worker URL wrong in `config.js`, or CORS origin mismatch |
| "Dispatch failed: 404" | Repo name wrong in `config.js` |
| "Dispatch failed: 403" | Token missing `repo` scope; re-auth |
| "Dispatch failed: 422" | Malformed payload; check browser console |
| Workflow runs but data.json doesn't update | Check `lisa-bot` has push permission (default `GITHUB_TOKEN` should work) |
| Workflow fails on push | Settings → Actions → General → Workflow permissions → **Read and write** |
