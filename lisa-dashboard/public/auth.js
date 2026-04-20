// auth.js — GitHub OAuth flow with remember-device via localStorage.
//
// Flow:
//   1. On load, check localStorage for a valid stored token.
//   2. If no token, show login gate.
//   3. Login button redirects to GitHub OAuth authorize URL.
//   4. GitHub redirects back with ?code=...
//   5. We POST the code to the token-exchange endpoint (Cloudflare Worker).
//   6. Worker returns an access token; we store it in localStorage.
//   7. Token is used for all subsequent API calls.
//
// Token lifetime: GitHub user-to-server tokens last 8 hours by default, but
// refresh tokens (if scope granted) extend to 6 months. We re-validate on
// load by calling /user; if it fails we drop the token and show the gate.
//
// SECURITY NOTES:
//   - The OAuth client_secret NEVER appears in client code. It lives only
//     as a Cloudflare Worker environment variable.
//   - localStorage is used for "remember me" — appropriate for a personal
//     dashboard. If you share the device with others, use the Logout button.
//   - Tokens are scoped to what your OAuth App requests. For this dashboard
//     we only need `repo` scope to trigger repository_dispatch.

const STORAGE_KEY = "lisa_gh_auth_v1";

const Auth = {
  _cached: null,

  get current() {
    if (this._cached) return this._cached;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed.token || !parsed.user) return null;
      this._cached = parsed;
      return parsed;
    } catch {
      return null;
    }
  },

  save(token, user) {
    const data = { token, user, savedAt: Date.now() };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    this._cached = data;
  },

  clear() {
    localStorage.removeItem(STORAGE_KEY);
    this._cached = null;
  },

  // Build the GitHub authorize URL. State is random; stored to verify on return.
  beginLogin() {
    const state = crypto.randomUUID();
    sessionStorage.setItem("lisa_oauth_state", state);
    const params = new URLSearchParams({
      client_id: window.LISA_CONFIG.oauthClientId,
      redirect_uri: window.location.origin + window.location.pathname,
      scope: "repo",
      state,
      allow_signup: "false",
    });
    window.location.href = `https://github.com/login/oauth/authorize?${params}`;
  },

  // Called on page load. If we have ?code=... in the URL, complete the exchange.
  async handleCallback() {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    if (!code) return false;

    const expected = sessionStorage.getItem("lisa_oauth_state");
    if (expected && expected !== state) {
      console.error("OAuth state mismatch");
      this._cleanUrl();
      return false;
    }
    sessionStorage.removeItem("lisa_oauth_state");

    try {
      const resp = await fetch(window.LISA_CONFIG.tokenExchangeUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (!resp.ok) throw new Error(`Exchange failed: ${resp.status}`);
      const { access_token } = await resp.json();
      if (!access_token) throw new Error("No access_token in exchange response");

      const user = await this._fetchUser(access_token);
      this.save(access_token, user);
    } catch (err) {
      console.error("Login failed:", err);
      showToast("Login failed. Check console for details.", "error");
    } finally {
      this._cleanUrl();
    }
    return true;
  },

  async _fetchUser(token) {
    const resp = await fetch("https://api.github.com/user", {
      headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" },
    });
    if (!resp.ok) throw new Error(`User fetch failed: ${resp.status}`);
    return resp.json();
  },

  async validate() {
    const auth = this.current;
    if (!auth) return false;
    try {
      await this._fetchUser(auth.token);
      return true;
    } catch {
      this.clear();
      return false;
    }
  },

  _cleanUrl() {
    const url = new URL(window.location.href);
    url.searchParams.delete("code");
    url.searchParams.delete("state");
    window.history.replaceState({}, "", url.toString());
  },

  // Check whether the current user is authorized to trigger sprint actions.
  isAuthorized() {
    const auth = this.current;
    if (!auth) return false;
    const allow = window.LISA_CONFIG.allowedUsers || [];
    if (allow.length === 0) return true;
    return allow.includes(auth.user.login);
  },

  // Fire a repository_dispatch event. This is how the dashboard triggers
  // sprint-action.yml workflows.
  async dispatch(eventType, payload) {
    const auth = this.current;
    if (!auth) throw new Error("Not authenticated");
    const resp = await fetch(
      `https://api.github.com/repos/${window.LISA_CONFIG.repo}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${auth.token}`,
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ event_type: eventType, client_payload: payload }),
      }
    );
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Dispatch failed: ${resp.status} ${text}`);
    }
  },
};

window.Auth = Auth;
