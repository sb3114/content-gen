#!/usr/bin/env python3
"""
Standalone token validation script.
Run outside Docker to quickly verify credentials:

    # 1. Source your secrets (never stored in .env)
    source ~/content-engine-secrets.sh

    # 2. Install deps into venv (one-time)
    python3 -m venv .venv && .venv/bin/pip install httpx python-dotenv -q

    # 3. Run
    .venv/bin/python3 scripts/validate_tokens.py

Returns exit code 0 if both are OK, 1 if either fails.
"""
import asyncio
import base64
import os
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("Missing dependency: pip install httpx")

# Load .env for non-sensitive config (site URLs, model names, etc.)
# Secrets (passwords, tokens, keys) are NOT in .env — they come from the
# shell environment exported before running this script.
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)  # shell env takes precedence
    except ImportError:
        # Manual parse as fallback (non-sensitive values only)
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD  = "\033[1m"


async def check_wordpress() -> bool:
    site_url = os.getenv("WORDPRESS_SITE_URL", "").rstrip("/")
    username = os.getenv("WORDPRESS_USERNAME", "")
    app_password = os.getenv("WORDPRESS_APP_PASSWORD", "")

    label = f"{BOLD}WordPress ({site_url or 'NOT SET'}){RESET}"

    if not site_url or not username or not app_password:
        print(f"{label}: {RED}❌ Not configured{RESET}")
        missing = [k for k, v in {
            "WORDPRESS_SITE_URL": site_url,
            "WORDPRESS_USERNAME": username,
            "WORDPRESS_APP_PASSWORD": app_password,
        }.items() if not v]
        print(f"   Missing: {', '.join(missing)}")
        return False

    credentials = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Step 1: Try creating a draft (real write-access test)
            resp = await client.post(
                f"{site_url}/wp-json/wp/v2/posts",
                headers=headers,
                json={
                    "title": "[Content Engine] Auth Validation Test",
                    "content": "Automated validation — safe to delete.",
                    "status": "draft",
                },
            )
            if resp.status_code in (200, 201):
                post = resp.json()
                post_id = post.get("id")
                post_url = post.get("link", "")
                # Clean up the test draft immediately
                await client.delete(
                    f"{site_url}/wp-json/wp/v2/posts/{post_id}",
                    headers=headers,
                    params={"force": "true"},
                )
                print(f"{label}: {GREEN}✅ Connected — write access confirmed{RESET}")
                print(f"   User  : {username}")
                print(f"   Site  : {site_url}/wp-json/wp/v2")
                print(f"   Auth  : Application Password (create + delete draft OK)")
                return True
            elif resp.status_code == 401:
                err = resp.json()
                print(f"{label}: {RED}❌ Authentication failed (401){RESET}")
                print(f"   Msg   : {err.get('message', 'Bad credentials')}")
                print(f"   Fix   : Regenerate the Application Password in WP Admin → Users → Profile")
                return False
            elif resp.status_code == 403 or (
                resp.status_code == 401 and 'rest_cannot_create' in resp.text
            ):
                print(f"{label}: {YELLOW}⚠️  Credentials valid but insufficient permissions{RESET}")
                print(f"   User '{username}' needs Editor or Administrator role to create posts.")
                print(f"   Fix : In WP Admin, change user role to Editor (or create a new Editor user).")
                return False
            else:
                try:
                    err = resp.json()
                    msg = err.get('message', resp.text[:120])
                    # Check for permission error disguised as 401
                    if 'rest_cannot_create' in resp.text or 'not allowed' in resp.text:
                        print(f"{label}: {YELLOW}⚠️  Credentials valid but user cannot create posts{RESET}")
                        print(f"   User '{username}' needs Editor or Administrator role.")
                        print(f"   Fix : In WP Admin → Users, set '{username}' role to Editor.")
                        return False
                except Exception:
                    msg = resp.text[:120]
                print(f"{label}: {RED}❌ HTTP {resp.status_code}{RESET}")
                print(f"   Error : {msg}")
                return False
    except Exception as exc:
        print(f"{label}: {RED}❌ Request failed — {exc}{RESET}")
        return False


async def check_linkedin() -> bool:
    access_token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    issued_at    = os.getenv("LINKEDIN_TOKEN_ISSUED_AT", "")

    label = f"{BOLD}LinkedIn{RESET}"

    if not access_token:
        print(f"{label}: {RED}❌ Not configured{RESET}")
        print("   Missing: LINKEDIN_ACCESS_TOKEN")
        print("   Run the app and visit http://localhost:8080/auth/linkedin")
        return False

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.linkedin.com/v2/userinfo", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                name = data.get("name", "")
                email = data.get("email", "")
                sub = data.get("sub", "")
                urn = f"urn:li:person:{sub}"

                days_remaining = None
                if issued_at:
                    from datetime import datetime, timezone
                    try:
                        issued = datetime.fromisoformat(issued_at)
                        elapsed = (datetime.now(timezone.utc) - issued).days
                        days_remaining = 60 - elapsed
                    except ValueError:
                        pass

                if days_remaining is not None and days_remaining < 10:
                    status = f"{YELLOW}⚠️  Connected (expires in {days_remaining} days){RESET}"
                else:
                    status = f"{GREEN}✅ Connected{RESET}"

                print(f"{label}: {status}")
                print(f"   Name  : {name}")
                print(f"   Email : {email}")
                print(f"   URN   : {urn}")
                if days_remaining is not None:
                    color = YELLOW if days_remaining < 10 else GREEN
                    print(f"   Token : {color}{days_remaining} days remaining{RESET}")
                else:
                    print(f"   Token : ~60 days (issued date unknown)")
                return True
            elif resp.status_code == 401:
                print(f"{label}: {RED}❌ Token expired or invalid (401){RESET}")
                print("   Re-authorize at http://localhost:8080/auth/linkedin")
                return False
            else:
                print(f"{label}: {RED}❌ HTTP {resp.status_code}{RESET}")
                print(f"   Body  : {resp.text[:120]}")
                return False
    except Exception as exc:
        print(f"{label}: {RED}❌ Request failed — {exc}{RESET}")
        return False


async def main() -> int:
    print(f"\n{BOLD}=== Content Engine — Token Validation ==={RESET}")
    print(f"Reading from: {env_path}\n")

    wp_ok = await check_wordpress()
    print()
    li_ok = await check_linkedin()
    print()

    if wp_ok and li_ok:
        print(f"{GREEN}{BOLD}All integrations are healthy! ✅{RESET}")
        return 0
    else:
        failed = []
        if not wp_ok: failed.append("WordPress")
        if not li_ok: failed.append("LinkedIn")
        print(f"{RED}{BOLD}Failed: {', '.join(failed)}{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
