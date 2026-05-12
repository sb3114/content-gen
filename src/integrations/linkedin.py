"""
LinkedIn OAuth 2.0 + UGC Posts API integration.
Docs: https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow
      https://learn.microsoft.com/en-us/linkedin/marketing/integrations/community-management/shares/ugc-post-api

Token lifetime: 60 days (standard), 12 months (with refresh token if granted).
"""
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from src.config import settings

LI_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LI_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LI_API = "https://api.linkedin.com/v2"
LI_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"  # OpenID Connect


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_auth_url(state: str) -> str:
    """
    Build the LinkedIn OAuth2 authorization URL.
    Scopes:
      openid        – required for /userinfo endpoint
      profile       – name, photo
      email         – email address
      w_member_social – permission to create UGC posts
    """
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "state": state,
        "scope": "openid profile email w_member_social",
    }
    return f"{LI_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for access token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            LI_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.linkedin_redirect_uri,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()  # {access_token, expires_in, [refresh_token]}


# ── LinkedIn client ───────────────────────────────────────────────────────────

class LinkedInClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def get_profile(self) -> dict:
        """
        Fetch the authenticated member's profile via OpenID Connect /userinfo.
        Returns {sub, name, given_name, family_name, email, picture}.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(LI_USERINFO_URL, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def validate_token(self) -> dict:
        """
        Light validation: call /userinfo and return a clean status dict.
        Returns {ok, name, urn} or raises on failure.
        """
        profile = await self.get_profile()
        sub = profile.get("sub", "")
        urn = f"urn:li:person:{sub}"

        # Calculate days until expiry if issued_at is known
        days_remaining = None
        if settings.linkedin_token_issued_at:
            try:
                issued = datetime.fromisoformat(settings.linkedin_token_issued_at)
                elapsed = (datetime.now(timezone.utc) - issued).days
                days_remaining = 60 - elapsed  # standard 60-day lifetime
            except ValueError:
                pass

        return {
            "ok": True,
            "name": profile.get("name", ""),
            "email": profile.get("email", ""),
            "urn": urn,
            "days_remaining": days_remaining,
        }

    async def post_article(
        self, post_text: str, article_url: str, author_urn: str
    ) -> dict:
        """Create a UGC post linking to the article."""
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": post_text},
                    "shareMediaCategory": "ARTICLE",
                    "media": [
                        {"status": "READY", "originalUrl": article_url}
                    ],
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LI_API}/ugcPosts",
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            post_id = resp.headers.get("x-restli-id", "")
            return {"post_id": post_id}


def get_client() -> LinkedInClient:
    if not settings.linkedin_access_token:
        raise ValueError(
            "LinkedIn not connected. Visit /auth/linkedin to connect."
        )
    return LinkedInClient(settings.linkedin_access_token)
