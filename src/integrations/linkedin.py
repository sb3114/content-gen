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

def get_auth_url(state: str, client_id: str = None) -> str:
    """Build the LinkedIn OAuth2 authorization URL."""
    c_id = client_id or settings.linkedin_client_id
    params = {
        "response_type": "code",
        "client_id": c_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "state": state,
        "scope": "openid profile email w_member_social",
    }
    return f"{LI_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, client_id: str = None, client_secret: str = None) -> dict:
    """Exchange authorization code for access token."""
    c_id = client_id or settings.linkedin_client_id
    c_secret = client_secret or settings.linkedin_client_secret
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            LI_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.linkedin_redirect_uri,
                "client_id": c_id,
                "client_secret": c_secret,
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

    async def upload_image(self, author_urn: str, image_bytes: bytes) -> str:
        """Register and upload image to LinkedIn. Returns the digitalmediaAsset URN."""
        register_payload = {
            "registerUploadRequest": {
                "owner": author_urn,
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "serviceRelationships": [
                    {
                        "identifier": "urn:li:userGeneratedContent",
                        "relationshipType": "OWNER"
                    }
                ]
            }
        }
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Register upload
            reg_resp = await client.post(
                f"{LI_API}/assets?action=registerUpload",
                json=register_payload,
                headers=self.headers
            )
            reg_resp.raise_for_status()
            reg_data = reg_resp.json()
            
            value = reg_data.get("value", {})
            asset_urn = value.get("asset", "")
            upload_mech = value.get("uploadMechanism", {})
            http_upload = upload_mech.get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
            upload_url = http_upload.get("uploadUrl", "")
            
            if not asset_urn or not upload_url:
                raise ValueError("Could not extract asset URN or upload URL from LinkedIn registration response.")
                
            # Step 2: Upload binary image data
            upload_headers = {
                "Content-Type": "image/jpeg"
            }
            up_resp = await client.put(
                upload_url,
                content=image_bytes,
                headers=upload_headers
            )
            up_resp.raise_for_status()
            
            return asset_urn

    async def post_article(
        self, post_text: str, author_urn: str, image_bytes: bytes | None = None
    ) -> dict:
        """Create a UGC post. If image_bytes is provided, upload the image first and attach it."""
        media_urn = None
        if image_bytes:
            media_urn = await self.upload_image(author_urn, image_bytes)

        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": post_text},
                    "shareMediaCategory": "IMAGE" if media_urn else "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }

        if media_urn:
            payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
                {
                    "status": "READY",
                    "media": media_urn,
                }
            ]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LI_API}/ugcPosts",
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            post_id = resp.headers.get("x-restli-id", "")
            return {"post_id": post_id}

    async def create_comment(
        self, post_urn: str, comment_text: str, author_urn: str
    ) -> dict:
        """Create a comment on a UGC post."""
        if not post_urn.startswith("urn:li:"):
            post_urn = f"urn:li:ugcPost:{post_urn}"

        payload = {
            "actor": author_urn,
            "object": post_urn,
            "message": {
                "text": comment_text
            }
        }

        encoded_urn = post_urn.replace(":", "%3A")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LI_API}/socialActions/{encoded_urn}/comments",
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()


    async def delete_post(self, post_id: str) -> bool:
        """
        Delete an existing UGC post by its URN/ID.
        LinkedIn's API does not support editing posts, so delete + re-create
        is the correct approach when republishing updated content.
        Returns True on success, False if the post was already gone (404).
        """
        # post_id may be a full URN or just the numeric part
        encoded_id = post_id.replace(":", "%3A") if ":" in post_id else post_id
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{LI_API}/ugcPosts/{encoded_id}",
                headers=self.headers,
            )
            if resp.status_code == 404:
                return False  # Already gone, that's fine
            resp.raise_for_status()
            return True


def get_client(db_settings=None) -> LinkedInClient:
    """Return a client using credentials from database or environment."""
    token = None
    if db_settings and db_settings.li_access_token:
        token = db_settings.li_access_token
    else:
        token = settings.linkedin_access_token
        
    if not token:
        raise ValueError(
            "LinkedIn not connected. Please go to Settings to connect."
        )
    return LinkedInClient(token)
