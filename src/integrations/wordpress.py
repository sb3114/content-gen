"""
Self-hosted WordPress REST API integration (Application Passwords).
Docs: https://developer.wordpress.com/2020/11/05/application-passwords-an-introduction/
API:  https://bondnow.net/wp-json/wp/v2/

Authentication: HTTP Basic Auth
    Username: WP username (e.g. contentAutomation)
    Password: Application Password generated in WP Admin → Users → Profile
              → Application Passwords  (spaces in the password are fine)
"""
import base64
import re
import unicodedata
from datetime import date

import httpx

from src.config import settings

# Self-hosted REST API base
WP_API = f"{settings.wordpress_site_url.rstrip('/')}/wp-json/wp/v2"


def _keyword_to_slug(text: str) -> str:
    """Convert a keyword or title to a clean URL slug (no dates, no IDs)."""
    slug = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    return re.sub(r"[\s_-]+", "-", slug)


def _auth_header(username: str = None, password: str = None) -> dict:
    """Build the Basic Auth header."""
    user = username or settings.wordpress_username
    pw = password or settings.wordpress_app_password
    
    if not user or not pw:
        raise ValueError(
            "WordPress credentials missing. Set them in Settings tab."
        )
    credentials = f"{user}:{pw}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


# ── WordPress client ──────────────────────────────────────────────────────────

class WordPressClient:
    def __init__(self, site_url: str = None, username: str = None, password: str = None):
        self.site_url = (site_url or settings.wordpress_site_url).rstrip('/')
        self.username = username or settings.wordpress_username
        self.base = f"{self.site_url}/wp-json/wp/v2"
        self.headers = {
            **_auth_header(self.username, password),
            "Content-Type": "application/json"
        }

    async def validate_token(self) -> dict:
        """
        Verify credentials have write access by creating a test draft
        and immediately deleting it.
        Returns {ok, username, site} or raises on failure.
        """
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.post(
                f"{self.base}/posts",
                headers=self.headers,
                json={
                    "title": "[Content Engine] Auth Validation Test",
                    "content": "Automated validation — safe to delete.",
                    "status": "draft",
                },
            )
            if resp.status_code in (200, 201):
                post = resp.json()
                post_id = post.get("id")
                # Clean up test draft immediately
                await client.delete(
                    f"{self.base}/posts/{post_id}",
                    headers=self.headers,
                    params={"force": "true"},
                )
                return {
                    "ok": True,
                    "username": self.username,
                    "site": self.site_url,
                }
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            msg = body.get("message", resp.text[:200])
            code = body.get("code", "")
            if "not allowed" in msg or code in ("rest_cannot_create", "rest_forbidden"):
                raise PermissionError(
                    f"User '{self.username}' does not have permission to create posts. "
                    "Change the WP user role to Editor or Administrator."
                )
            resp.raise_for_status()

    async def get_categories(self) -> list[dict]:
        """Return list of {id, name, slug} for the site."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/categories",
                headers=self.headers,
                params={"per_page": 100},
            )
            resp.raise_for_status()
            return [
                {"id": c["id"], "name": c["name"], "slug": c["slug"]}
                for c in resp.json()
            ]

    async def create_draft(
        self,
        title: str,
        html_content: str,
        focus_keyword: str,
        meta_description: str,
        tags: list[str],
        category_ids: list[int] | None = None,
        author_id: int | None = None,
        author_name: str = "",
    ) -> dict:
        """
        Create a post with status=draft on the self-hosted WP site.

        SEO fields (Yoast / RankMath) are written via the `meta` key.
        Article JSON-LD schema is prepended to the post body.
        Returns {post_id, url, edit_url}.
        """
        # Resolve tag names → IDs (create if missing)
        tag_ids = await self._resolve_tags(tags)

        # Build slug from focus keyword (clean, readable, no dates)
        slug = _keyword_to_slug(focus_keyword or title)

        # Build Article JSON-LD schema block
        today_iso = date.today().isoformat()
        safe_title = title.replace('"', '\\"')
        safe_desc = meta_description.replace('"', '\\"')
        safe_author = author_name.replace('"', '\\"')
        schema_json = (
            f'{{"@context":"https://schema.org","@type":"Article",'  
            f'"headline":"{safe_title}",'  
            f'"author":{{"@type":"Person","name":"{safe_author}"}},'  
            f'"datePublished":"{today_iso}","dateModified":"{today_iso}",'  
            f'"description":"{safe_desc}"}}'
        )
        schema_block = f'<script type="application/ld+json">{schema_json}</script>\n'
        full_content = schema_block + html_content

        payload: dict = {
            "title": title,
            "content": full_content,
            "status": "draft",
            "slug": slug,
            "tags": tag_ids,
            "meta": {
                # Yoast SEO fields
                "_yoast_wpseo_focuskw": focus_keyword,
                "_yoast_wpseo_metadesc": meta_description,
                # RankMath fields (ignored if plugin absent)
                "rank_math_focus_keyword": focus_keyword,
                "rank_math_description": meta_description,
            },
        }
        if category_ids:
            payload["categories"] = category_ids
        if author_id:
            payload["author"] = author_id

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/posts",
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "post_id": str(data["id"]),
                "url": data.get("link", ""),
                "edit_url": (
                    f"{self.site_url}"
                    f"/wp-admin/post.php?post={data['id']}&action=edit"
                ),
            }

    async def _resolve_tags(self, tag_names: list[str]) -> list[int]:
        """Look up tag IDs by name; create any that don't exist yet."""
        if not tag_names:
            return []
        ids: list[int] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for name in tag_names[:10]:  # WP limit
                resp = await client.get(
                    f"{self.base}/tags",
                    headers=self.headers,
                    params={"search": name, "per_page": 1},
                )
                resp.raise_for_status()
                results = resp.json()
                if results:
                    ids.append(results[0]["id"])
                else:
                    # Create the tag
                    create = await client.post(
                        f"{self.base}/tags",
                        json={"name": name},
                        headers=self.headers,
                    )
                    if create.status_code in (200, 201):
                        ids.append(create.json()["id"])
        return ids


def get_client(db_settings=None) -> WordPressClient:
    """Return a client using credentials from database or environment."""
    if db_settings and db_settings.wp_username and db_settings.wp_app_password:
        return WordPressClient(
            site_url=db_settings.wp_site_url,
            username=db_settings.wp_username,
            password=db_settings.wp_app_password
        )
    
    # Fallback to env
    if not settings.wordpress_username or not settings.wordpress_app_password:
        raise ValueError(
            "WordPress not configured. Please set credentials in Settings."
        )
    return WordPressClient()
