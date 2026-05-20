from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Gemini
    gemini_api_key: str = ""
    gemini_planning_model: str = "gemini-2.0-flash"
    gemini_writing_model: str = "gemini-2.5-pro"

    # DataForSEO
    dataforseo_login: Optional[str] = None
    dataforseo_password: Optional[str] = None
    dataforseo_location_code: int = 2826  # UK (2826) by default to target UK/EU
    dataforseo_language_name: str = "English"
    dataforseo_language_code: str = "en"

    # WordPress (self-hosted, Application Passwords)
    wordpress_site_url: str = "https://bondnow.net"  # no trailing slash
    wordpress_username: str = ""
    wordpress_app_password: str = ""  # from WP Admin → Users → Application Passwords

    # LinkedIn
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8080/auth/linkedin/callback"
    linkedin_access_token: Optional[str] = None
    linkedin_person_urn: Optional[str] = None
    linkedin_token_issued_at: Optional[str] = None  # ISO datetime, written after OAuth

    # Database
    database_url: str = "postgresql+asyncpg://content:content@db:5432/content_engine"

    # App
    secret_key: str = "change-me-to-a-random-32-char-string"
    debug: bool = False


settings = Settings()
