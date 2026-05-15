"""
Brevo (Sendinblue) API v3 integration for Email Newsletters.
"""
import httpx
import logging
from src.config import settings

logger = logging.getLogger(__name__)

BREVO_API = "https://api.brevo.com/v3"

class BrevoClient:
    def __init__(self, api_key: str, sender_name: str = None, sender_email: str = None):
        self.api_key = api_key
        self.sender_name = sender_name
        self.sender_email = sender_email
        self.headers = {
            "api-key": self.api_key,
            "content-type": "application/json",
            "accept": "application/json"
        }

    async def _get_sender_id(self, client: httpx.AsyncClient) -> int:
        """Find the sender ID for the configured email."""
        resp = await client.get(f"{BREVO_API}/senders", headers=self.headers)
        if resp.status_code == 200:
            senders = resp.json().get("senders", [])
            for s in senders:
                if s["email"].lower() == self.sender_email.lower() and s.get("active"):
                    return s["id"]
        return None

    async def create_and_send_campaign(
        self, 
        name: str, 
        subject: str, 
        preheader: str,
        html_content: str, 
        list_ids: list[int]
    ) -> dict:
        """
        Create an email campaign using template ID 57 and send it immediately.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            # Try to get verified sender ID first
            sender_id = await self._get_sender_id(client)
            
            # Apply manual escaping for double quotes and line breaks as requested
            escaped_html = html_content.replace('"', '\\"').replace('\n', '\\n')
            
            # 1. Create the campaign using template
            payload = {
                "name": name,
                "templateId": 57,
                "subject": subject,
                "params": {
                    "subject": subject,
                    "preview": preheader,
                    "body": escaped_html
                },
                "recipients": {"listIds": list_ids}
            }
            
            if sender_id:
                payload["sender"] = {"id": sender_id}
            
            create_resp = await client.post(
                f"{BREVO_API}/emailCampaigns",
                headers=self.headers,
                json=payload
            )
            if create_resp.status_code >= 400:
                logger.error(f"Brevo API Error: {create_resp.text}")
            create_resp.raise_for_status()
            campaign_id = create_resp.json()["id"]

            # 2. Send the campaign immediately
            send_resp = await client.post(
                f"{BREVO_API}/emailCampaigns/{campaign_id}/sendNow",
                headers=self.headers
            )
            send_resp.raise_for_status()
            
            return {"campaign_id": str(campaign_id)}

    async def get_lists(self) -> list[dict]:
        """Fetch all contact lists from Brevo."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BREVO_API}/contacts/lists", headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                return [{"id": l["id"], "name": l["name"]} for l in data.get("lists", [])]
            logger.error(f"Failed to fetch Brevo lists: {resp.text}")
            return []

    async def validate_credentials(self) -> dict:
        """Check if API key and sender details are valid."""
        async with httpx.AsyncClient(timeout=10) as client:
            # 1. Check Account / API Key
            acc_resp = await client.get(f"{BREVO_API}/account", headers=self.headers)
            if acc_resp.status_code != 200:
                return {"ok": False, "error": "Invalid API Key"}
            
            # 2. Check if Sender Email is verified
            sender_resp = await client.get(f"{BREVO_API}/senders", headers=self.headers)
            if sender_resp.status_code == 200:
                senders = sender_resp.json().get("senders", [])
                verified_emails = [s["email"].lower() for s in senders if s.get("active")]
                if self.sender_email.lower() not in verified_emails:
                    return {
                        "ok": False, 
                        "error": f"Sender email '{self.sender_email}' is not verified or active in Brevo. Verified senders: {', '.join(verified_emails)}"
                    }
            
            data = acc_resp.json()
            return {
                "ok": True,
                "company": data.get("companyName"),
                "email": self.sender_email
            }

def get_client(db_settings=None) -> BrevoClient:
    api_key = (db_settings.brevo_api_key if db_settings else None)
    sender_name = (db_settings.brevo_sender_name if db_settings else None)
    sender_email = (db_settings.brevo_sender_email if db_settings else None)
    
    if not api_key or not sender_email:
        raise ValueError("Brevo API Key and Sender Email are required in Settings.")
        
    return BrevoClient(api_key, sender_name, sender_email)
