// Dashboard configuration — edit these values after setting up your OAuth app.
// See docs/SETUP.md for instructions.

window.LISA_CONFIG = {
  // The GitHub repo that stores sprint data and receives dispatches.
  repo: "outdoorbengal/lisa-dashboard",

  // GitHub OAuth App client ID. Create one at:
  //   https://github.com/settings/developers → New OAuth App
  // Callback URL should be your Pages URL, e.g.:
  //   https://outdoorbengal.github.io/lisa-dashboard/
  oauthClientId: "REPLACE_WITH_YOUR_OAUTH_CLIENT_ID",

  // URL of your token-exchange endpoint (small serverless function).
  // See docs/SETUP.md for a ready-to-deploy Cloudflare Worker.
  // The dashboard sends the OAuth `code` here; the worker exchanges it
  // for an access token using your OAuth client_secret (stored server-side).
  tokenExchangeUrl: "https://lisa-oauth.YOUR-WORKER.workers.dev/exchange",

  // Only users matching this GitHub login can trigger sprint actions.
  // Leave as empty array to allow any authenticated user with push access
  // to the repo (recommended for solo use).
  allowedUsers: [],  // e.g. ["outdoorbengal"]
};
