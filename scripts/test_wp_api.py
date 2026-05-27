import asyncio
import base64
import httpx
from sqlmodel import select
from src.database import AsyncSessionLocal
from src.models.settings import CompanySettings

async def main():
    print("=== WordPress API Diagnostic ===")
    async with AsyncSessionLocal() as session:
        settings_obj = await session.get(CompanySettings, 1)
        if not settings_obj:
            print("No settings found in DB.")
            return

        site_url = settings_obj.wp_site_url or ""
        username = settings_obj.wp_username or ""
        password = settings_obj.wp_app_password or ""

        print(f"Site URL: {site_url}")
        print(f"Username: {username}")
        print(f"Password: {'*' * len(password)}")
        print(f"Author ID: {settings_obj.wp_author_id}")

        if not site_url or not username or not password:
            print("Missing WordPress configuration in DB.")
            return

        base = f"{site_url.rstrip('/')}/wp-json/wp/v2"
        credentials = f"{username}:{password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0" # genericrest
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            print("\n1. Testing authentication status via /users/me...")
            me_resp = await client.get(f"{base}/users/me", headers=headers)
            print(f"Status Code: {me_resp.status_code}")
            try:
                print("Body:", me_resp.json())
            except Exception:
                print("Body:", me_resp.text[:500])

            print("\n2. Testing post creation via /posts...")
            post_resp = await client.post(
                f"{base}/posts",
                headers=headers,
                json={
                    "title": "[Content Engine] API Diagnostic Test",
                    "content": "Automated validation — safe to delete.",
                    "status": "draft",
                },
            )
            print(f"Status Code: {post_resp.status_code}")
            try:
                print("Body:", post_resp.json())
            except Exception:
                print("Body:", post_resp.text[:500])

            if post_resp.status_code in (200, 201):
                post_id = post_resp.json().get("id")
                print(f"Success! Created post ID: {post_id}. Cleaning up...")
                del_resp = await client.delete(
                    f"{base}/posts/{post_id}",
                    headers=headers,
                    params={"force": "true"},
                )
                print(f"Cleanup Status Code: {del_resp.status_code}")

if __name__ == "__main__":
    asyncio.run(main())
