"""
OAuth flows and token validation.

WordPress:  No OAuth — uses Application Passwords (Basic Auth over HTTPS).
            Credentials are injected as environment variables at container startup.
            No secrets are stored in .env or any committed file.

LinkedIn:   Full OAuth 2.0 Authorization Code flow.
            After the callback the tokens are displayed for the user to add
            to their ~/content-engine-secrets.sh and re-inject on next startup.
"""
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.integrations import linkedin as li_integration
from src.integrations import wordpress as wp_integration

router = APIRouter(prefix="/auth")

# In-memory CSRF state store (fine for single-user app)
_states: dict[str, str] = {}


# ── WordPress status (no OAuth needed) ───────────────────────────────────────

@router.get("/wordpress/validate")
async def wp_validate() -> dict:
    """
    Live check: attempt to create+delete a test draft using stored credentials.
    Returns JSON {ok, username, site} or {ok: false, error: ...}.
    """
    try:
        client = wp_integration.get_client()
        info = await client.validate_token()
        return info
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── LinkedIn OAuth 2.0 ────────────────────────────────────────────────────────

@router.get("/linkedin")
async def li_connect():
    """Redirect browser to LinkedIn consent page."""
    state = secrets.token_urlsafe(16)
    _states[state] = "linkedin"
    url = li_integration.get_auth_url(state)
    return RedirectResponse(url)


@router.get("/linkedin/callback")
async def li_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    if error:
        return HTMLResponse(
            f"<h2>LinkedIn OAuth Error</h2><pre>{error}</pre>",
            status_code=400,
        )
    if state not in _states:
        return HTMLResponse("Invalid or expired state. Please try again.", status_code=400)
    del _states[state]

    # Exchange authorization code for access token
    token_data = await li_integration.exchange_code(code)
    access_token = token_data.get("access_token", "")
    expires_in = token_data.get("expires_in", 5183944)  # ~60 days default

    person_urn = ""
    name = ""
    email = ""
    issued_at = datetime.now(timezone.utc).isoformat()

    if access_token:
        # Fetch profile via OpenID Connect /userinfo
        try:
            client = li_integration.LinkedInClient(access_token)
            profile = await client.get_profile()
            sub = profile.get("sub", "")
            person_urn = f"urn:li:person:{sub}"
            name = profile.get("name", "")
            email = profile.get("email", "")
        except Exception as exc:
            name = f"(profile fetch failed: {exc})"

    days = expires_in // 86400

    # Tokens are NOT written to any file — display them for the user to
    # add to their ~/content-engine-secrets.sh and re-inject on restart.
    return HTMLResponse(f"""
    <html><head><title>LinkedIn Connected</title></head>
    <body style="font-family:monospace;padding:2rem;background:#0f0f17;color:#e2e8f0">
    <h2 style="color:#4ade80">&#x2705; LinkedIn Connected!</h2>
    <p><strong>Name:</strong> {name}</p>
    <p><strong>Email:</strong> {email}</p>
    <p><strong>Person URN:</strong> <code>{person_urn}</code></p>
    <p style="color:#94a3b8">Token expires in ~{days} days.</p>
    <hr style="border-color:#2d2d44;margin:1.5rem 0">
    <h3 style="color:#fbbf24">&#x26A0;&#xFE0F; Add these to your secrets file and restart</h3>
    <p style="color:#94a3b8;font-size:0.85rem">
      Edit <code>~/content-engine-secrets.sh</code>, update these three lines,
      then re-source and restart the container:
    </p>
    <pre style="background:#1e1e2e;padding:1rem;border-radius:8px;color:#4ade80;user-select:all">
export LINKEDIN_ACCESS_TOKEN={access_token}
export LINKEDIN_PERSON_URN={person_urn}
export LINKEDIN_TOKEN_ISSUED_AT={issued_at}</pre>
    <p style="color:#94a3b8;font-size:0.8rem">
      Then restart:<br>
      <code>source ~/content-engine-secrets.sh &amp;&amp; docker compose up -d --force-recreate app</code>
    </p>
    <a href="/auth/validate" style="color:#a78bfa">&#x2190; Back to token status</a>
    </body></html>
    """)


@router.get("/linkedin/validate")
async def li_validate() -> dict:
    """
    Live check: call /userinfo with stored token.
    Returns JSON {ok, name, email, urn, days_remaining} or {ok: false, error: ...}.
    """
    try:
        client = li_integration.get_client()
        info = await client.validate_token()
        return info
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Combined status page ──────────────────────────────────────────────────────

@router.get("/validate", response_class=HTMLResponse)
async def validate_page(request: Request):
    """
    Browser-friendly status page showing live connectivity for both integrations.
    Fetches /auth/wordpress/validate and /auth/linkedin/validate via JS in the browser.
    """
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Integration Status — Content Engine</title>
  <style>
    :root {
      --bg: #0f0f17; --surface: #1e1e2e; --border: #2d2d44;
      --text: #e2e8f0; --muted: #94a3b8;
      --green: #4ade80; --red: #f87171; --yellow: #fbbf24;
      --purple: #a78bfa;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text);
           font-family: 'Inter', system-ui, sans-serif;
           min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .container { width: 100%; max-width: 640px; padding: 2rem 1rem; }
    h1 { font-size: 1.5rem; color: var(--purple); margin-bottom: 0.25rem; }
    .subtitle { color: var(--muted); font-size: 0.875rem; margin-bottom: 2rem; }
    .card { background: var(--surface); border: 1px solid var(--border);
            border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; }
    .card-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
    .logo { font-size: 1.75rem; }
    .card-title { font-size: 1.1rem; font-weight: 600; }
    .status-badge { margin-left: auto; padding: 0.25rem 0.75rem; border-radius: 999px;
                    font-size: 0.75rem; font-weight: 600; }
    .badge-checking { background: #27272a; color: var(--muted); }
    .badge-ok     { background: #052e16; color: var(--green); }
    .badge-error  { background: #2a0a0a; color: var(--red); }
    .badge-warn   { background: #2a1a00; color: var(--yellow); }
    .info-row { display: flex; justify-content: space-between; font-size: 0.85rem;
                padding: 0.35rem 0; border-bottom: 1px solid var(--border); }
    .info-row:last-child { border-bottom: none; }
    .info-label { color: var(--muted); }
    .info-value { color: var(--text); font-family: monospace; }
    .btn { display: inline-block; margin-top: 1rem; padding: 0.5rem 1.25rem;
           background: var(--purple); color: white; border-radius: 8px;
           text-decoration: none; font-size: 0.85rem; font-weight: 600; }
    .btn:hover { opacity: 0.85; }
    .warn-box { background: #2a1a00; border: 1px solid #78350f; border-radius: 8px;
                padding: 0.75rem 1rem; font-size: 0.8rem; color: var(--yellow); margin-top: 1rem; }
    .error-box { color: var(--red); font-size: 0.85rem; margin-bottom: 0.5rem; }
    .hint-box { color: var(--muted); font-size: 0.8rem; margin-top: 0.5rem; }
    .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--muted);
               border-top-color: var(--purple); border-radius: 50%;
               animation: spin 0.8s linear infinite; vertical-align: middle; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .back { margin-top: 1.5rem; }
    .back a { color: var(--purple); font-size: 0.85rem; text-decoration: none; }
  </style>
</head>
<body>
<div class="container">
  <h1>&#x1F50C; Integration Status</h1>
  <p class="subtitle">Live connectivity check &mdash; secrets injected via host environment, never stored in .env</p>

  <!-- WordPress Card -->
  <div class="card">
    <div class="card-header">
      <span class="logo">&#x1F310;</span>
      <span class="card-title">WordPress &mdash; bondnow.net</span>
      <span class="status-badge badge-checking" id="wp-badge">
        <span class="spinner"></span> Checking&hellip;
      </span>
    </div>
    <div id="wp-details"><p style="color:var(--muted);font-size:0.85rem">Loading&hellip;</p></div>
  </div>

  <!-- LinkedIn Card -->
  <div class="card">
    <div class="card-header">
      <span class="logo">&#x1F4BC;</span>
      <span class="card-title">LinkedIn</span>
      <span class="status-badge badge-checking" id="li-badge">
        <span class="spinner"></span> Checking&hellip;
      </span>
    </div>
    <div id="li-details"><p style="color:var(--muted);font-size:0.85rem">Loading&hellip;</p></div>
  </div>

  <div class="back"><a href="/">&#x2190; Back to dashboard</a></div>
</div>

<script>
async function checkWP() {
  const badge = document.getElementById('wp-badge');
  const details = document.getElementById('wp-details');
  try {
    const r = await fetch('/auth/wordpress/validate');
    const d = await r.json();
    if (d.ok) {
      badge.className = 'status-badge badge-ok';
      badge.textContent = '\\u2705 Connected';
      details.innerHTML = `
        <div class="info-row"><span class="info-label">User</span><span class="info-value">${d.username}</span></div>
        <div class="info-row"><span class="info-label">Site</span><span class="info-value">${d.site}/wp-json/wp/v2</span></div>
        <div class="info-row"><span class="info-label">Auth</span><span class="info-value">Application Password (write-access confirmed)</span></div>
      `;
    } else {
      throw new Error(d.error || 'Unknown error');
    }
  } catch(e) {
    badge.className = 'status-badge badge-error';
    badge.textContent = '\\u274C Not Connected';
    details.innerHTML = `
      <p class="error-box">${e.message}</p>
      <p class="hint-box">Set <code>WORDPRESS_USERNAME</code> and <code>WORDPRESS_APP_PASSWORD</code>
      in <code>~/content-engine-secrets.sh</code> and restart the container.<br>
      The WP user must have <strong>Editor</strong> or <strong>Administrator</strong> role.</p>
    `;
  }
}

async function checkLI() {
  const badge = document.getElementById('li-badge');
  const details = document.getElementById('li-details');
  try {
    const r = await fetch('/auth/linkedin/validate');
    const d = await r.json();
    if (d.ok) {
      const days = d.days_remaining;
      const warn = days !== null && days < 10;
      badge.className = warn ? 'status-badge badge-warn' : 'status-badge badge-ok';
      badge.textContent = warn ? `\\u26A0\\uFE0F Expires in ${days}d` : '\\u2705 Connected';
      details.innerHTML = `
        <div class="info-row"><span class="info-label">Name</span><span class="info-value">${d.name}</span></div>
        <div class="info-row"><span class="info-label">Email</span><span class="info-value">${d.email}</span></div>
        <div class="info-row"><span class="info-label">Person URN</span><span class="info-value">${d.urn}</span></div>
        <div class="info-row"><span class="info-label">Token</span>
          <span class="info-value" style="color:${warn ? 'var(--yellow)' : 'var(--green)'}">
            ${days !== null ? days + ' days remaining' : '~60 days (issued date unknown)'}
          </span></div>
      ` + (warn ? `<div class="warn-box">&#x26A0;&#xFE0F; Token expiring soon. <a href="/auth/linkedin" style="color:var(--yellow)">Re-authorize &#x2192;</a></div>` : '');
    } else {
      throw new Error(d.error || 'Unknown error');
    }
  } catch(e) {
    badge.className = 'status-badge badge-error';
    badge.textContent = '\\u274C Not Connected';
    details.innerHTML = `
      <p class="error-box">${e.message}</p>
      <p class="hint-box">Complete OAuth to get a token, then add it to <code>~/content-engine-secrets.sh</code>.</p>
      <a href="/auth/linkedin" class="btn">Connect LinkedIn &#x2192;</a>
    `;
  }
}

checkWP();
checkLI();
</script>
</body>
</html>
""")
