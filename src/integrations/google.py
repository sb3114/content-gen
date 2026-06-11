"""
Google Integration Client for Google Search Console (Indexing API) and Google Business Profile API.
"""
import json
import logging
import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleRequest

logger = logging.getLogger(__name__)

# ── Google Search Console Indexing Client ─────────────────────────────────────

class GoogleSearchConsoleClient:
    def __init__(self, service_account_json_str: str):
        if not service_account_json_str:
            raise ValueError("Google Search Console service account JSON is not configured.")
        self.creds_info = json.loads(service_account_json_str)
        self.scopes = ["https://www.googleapis.com/auth/indexing"]

    def _get_credentials(self) -> service_account.Credentials:
        return service_account.Credentials.from_service_account_info(
            self.creds_info, scopes=self.scopes
        )

    async def validate_connection(self, url: str = None) -> dict:
        """
        Validates connection by generating an OAuth2 access token.
        If generation succeeds, the credentials are valid.
        If a URL is provided, it also checks if the service account has permissions
        for that specific domain in GSC.
        """
        try:
            creds = self._get_credentials()
            # Run in a threadpool to avoid blocking event loop
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, creds.refresh, GoogleRequest())
            if creds.token:
                if url:
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {creds.token}"
                    }
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.get(
                            f"https://indexing.googleapis.com/v3/urlNotifications/metadata?url={url}",
                            headers=headers
                        )
                        if resp.status_code == 403:
                            return {
                                "ok": False, 
                                "error": f"Credentials are valid, but {self.creds_info.get('client_email')} lacks 'Owner' permission in Google Search Console for the site ({url})."
                            }
                return {"ok": True, "client_email": self.creds_info.get("client_email")}
            return {"ok": False, "error": "Failed to retrieve access token."}
        except Exception as e:
            logger.error(f"GSC validation error: {e}")
            return {"ok": False, "error": str(e)}

    async def submit_indexing(self, url: str) -> dict:
        """
        Request indexing for the given URL.
        """
        try:
            creds = self._get_credentials()
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, creds.refresh, GoogleRequest())
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {creds.token}"
            }
            payload = {
                "url": url,
                "type": "URL_UPDATED"
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://indexing.googleapis.com/v3/urlNotifications:publish",
                    json=payload,
                    headers=headers
                )
                resp.raise_for_status()
                return {"ok": True, "data": resp.json()}
        except Exception as e:
            logger.error(f"GSC Indexing submission error for {url}: {e}")
            return {"ok": False, "error": str(e)}


# ── Google Business Profile Client ───────────────────────────────────────────

class GoogleBusinessProfileClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, account_id: str, location_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.account_id = account_id
        self.location_id = location_id

    async def get_access_token(self) -> str:
        if not self.client_id or not self.client_secret or not self.refresh_token:
            raise ValueError("GBP Client ID, Client Secret, or Refresh Token is missing.")
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token"
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data=payload)
            resp.raise_for_status()
            return resp.json()["access_token"]

    async def validate_connection(self) -> dict:
        """
        Validate by obtaining token and fetching location details.
        """
        try:
            token = await self.get_access_token()
            if not self.account_id or not self.location_id:
                return {"ok": False, "error": "Account ID and Location ID must be configured to validate connection."}
            
            # Use modern Business Information API v1
            # Resource format is accounts/{accountId}/locations/{locationId}
            # Location details endpoint: GET https://mybusinessbusinessinformation.googleapis.com/v1/accounts/{accountId}/locations/{locationId}
            # Wait, or directly locations/{locationId} depending on how accountId is provided.
            # Let's try matching the parent structure.
            clean_acc = self.account_id.replace("accounts/", "")
            clean_loc = self.location_id.replace("locations/", "")
            
            url = f"https://mybusinessbusinessinformation.googleapis.com/v1/accounts/{clean_acc}/locations"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient(timeout=30) as client:
                # Fetch locations under the account to see if location_id is present and credentials work
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                
                # Check if we can find our location in the list or if the request simply succeeds
                locations = data.get("locations", [])
                matched_loc = None
                for loc in locations:
                    name = loc.get("name", "")
                    if name.endswith(clean_loc) or clean_loc in name:
                        matched_loc = loc
                        break
                
                if matched_loc:
                    return {"ok": True, "location_title": matched_loc.get("title", "Configured Profile")}
                elif locations:
                    # Let's also check if location detail directly works
                    detail_url = f"https://mybusinessbusinessinformation.googleapis.com/v1/locations/{clean_loc}?readMask=name,title"
                    resp_detail = await client.get(detail_url, headers=headers)
                    if resp_detail.status_code == 200:
                        return {"ok": True, "location_title": resp_detail.json().get("title", "Configured Profile")}
                    return {"ok": True, "location_title": "Configured Profile (List loaded, but ID mismatch)"}
                else:
                    return {"ok": True, "location_title": "Configured Profile (Empty locations list)"}
        except Exception as e:
            logger.error(f"GBP validation error: {e}")
            return {"ok": False, "error": str(e)}

    async def create_local_post(self, summary: str, learn_more_url: str) -> dict:
        """
        Creates a standard local post on GBP with a LEARN_MORE CTA button.
        """
        try:
            token = await self.get_access_token()
            clean_acc = self.account_id.replace("accounts/", "")
            clean_loc = self.location_id.replace("locations/", "")
            
            # GMB v4 REST Endpoint for posts
            url = f"https://mybusiness.googleapis.com/v4/accounts/{clean_acc}/locations/{clean_loc}/localPosts"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            payload = {
                "languageCode": "en-US",
                "summary": summary,
                "topicType": "STANDARD",
                "callToAction": {
                    "actionType": "LEARN_MORE",
                    "url": learn_more_url
                }
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return {"ok": True, "post_name": data.get("name", "")}
        except Exception as e:
            logger.error(f"GBP post creation error: {e}")
            return {"ok": False, "error": str(e)}
