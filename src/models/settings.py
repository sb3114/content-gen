from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field, Column, Text

class CompanySettings(SQLModel, table=True):
    __tablename__ = "company_settings"

    id: int = Field(default=1, primary_key=True)
    
    # LLM Settings
    llm_provider: str = Field(default="gemini")
    claude_setup_token: Optional[str] = Field(default=None)
    allow_fallback_to_haiku: bool = Field(default=True)
    rate_limit_banner: Optional[str] = Field(default=None)
    rate_limit_until: Optional[datetime] = Field(default=None)
    gemini_api_key: Optional[str] = Field(default=None)
    claude_api_key: Optional[str] = Field(default=None)
    gemini_only_image_generation: bool = Field(default=True)


    
    # Brand Context
    marketing_strategy: Optional[str] = Field(default=None, sa_column=Column(Text))
    icp: Optional[str] = Field(default=None, sa_column=Column(Text))  # legacy — kept for migration safety
    icp_context: Optional[str] = Field(default=None, sa_column=Column(Text))  # consolidated free-text ICP / personas / pain points
    core_pillars: Optional[str] = Field(default=None, sa_column=Column(Text))
    tone_of_voice: Optional[str] = Field(default=None, sa_column=Column(Text))
    audiences: Optional[str] = Field(default=None, sa_column=Column(Text))   # legacy
    company_description: Optional[str] = Field(default=None, sa_column=Column(Text))
    summarized_context: Optional[str] = Field(default=None, sa_column=Column(Text))

    # WordPress Credentials
    wp_site_url: Optional[str] = Field(default=None)
    wp_username: Optional[str] = Field(default=None)
    wp_app_password: Optional[str] = Field(default=None)
    wp_author_id: Optional[int] = Field(default=None)    # numeric WP user ID for published posts
    wp_author_name: Optional[str] = Field(default=None)  # display name for Article schema markup
    yoast_plugin: bool = Field(default=False)

    # LinkedIn Credentials
    li_client_id: Optional[str] = Field(default=None)
    li_client_secret: Optional[str] = Field(default=None)
    li_access_token: Optional[str] = Field(default=None)
    li_person_urn: Optional[str] = Field(default=None)
    li_token_expires_at: Optional[str] = Field(default=None)

    # Brevo Credentials
    brevo_api_key: Optional[str] = Field(default=None)
    brevo_list_id: Optional[int] = Field(default=None)
    brevo_sender_email: Optional[str] = Field(default=None)
    brevo_sender_name: Optional[str] = Field(default=None)

    # DataForSEO Credentials
    dataforseo_login: Optional[str] = Field(default=None)
    dataforseo_password: Optional[str] = Field(default=None)

    # Google Search Console Indexing Credentials
    gsc_service_account_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Google Business Profile Credentials
    gbp_access_token: Optional[str] = Field(default=None, sa_column=Column(Text))
    gbp_account_id: Optional[str] = Field(default=None)
    gbp_location_id: Optional[str] = Field(default=None)
    gbp_client_id: Optional[str] = Field(default=None)
    gbp_client_secret: Optional[str] = Field(default=None)

    # Queue Processing Time Windows
    queue_start_hour: Optional[int] = Field(default=None)
    queue_end_hour: Optional[int] = Field(default=None)
    queue_timezone: Optional[str] = Field(default="Europe/London")


