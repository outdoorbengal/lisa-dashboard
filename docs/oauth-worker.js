/**
 * lisa-oauth-worker
 *
 * Tiny Cloudflare Worker that exchanges a GitHub OAuth `code` for an access
 * token. This exists because the OAuth client_secret must never appear in
 * browser code. The Worker holds it as an environment variable and does the
 * exchange server-side.
 *
 * DEPLOY:
 *   npm create cloudflare@latest lisa-oauth
 *   # replace src/index.js with this file
 *   # set secrets:
 *   npx wrangler secret put GITHUB_CLIENT_ID
 *   npx wrangler secret put GITHUB_CLIENT_SECRET
 *   npx wrangler secret put ALLOWED_ORIGIN   # e.g. https://outdoorbengal.github.io
 *   npx wrangler deploy
 *
 * CONFIGURE GITHUB OAUTH APP:
 *   https://github.com/settings/developers → New OAuth App
 *     Application name: Lisa Dashboard
 *     Homepage URL:     https://outdoorbengal.github.io/lisa-dashboard
 *     Authorization callback URL:
 *       https://outdoorbengal.github.io/lisa-dashboard/
 *   Copy the Client ID into public/config.js
 *   Copy the Client Secret into the Worker secret above.
 */

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(env) });
    }

    if (request.method !== "POST") {
      return json({ error: "method_not_allowed" }, 405, env);
    }

    const url = new URL(request.url);
    if (url.pathname !== "/exchange") {
      return json({ error: "not_found" }, 404, env);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "invalid_json" }, 400, env);
    }

    const { code } = body || {};
    if (!code || typeof code !== "string") {
      return json({ error: "missing_code" }, 400, env);
    }

    const resp = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        code,
      }),
    });

    const data = await resp.json();
    if (data.error) {
      return json({ error: data.error, description: data.error_description }, 400, env);
    }

    return json({ access_token: data.access_token, scope: data.scope }, 200, env);
  },
};

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

function json(obj, status, env) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(env) },
  });
}
