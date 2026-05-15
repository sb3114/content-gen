from typing import Optional
from sqlmodel import SQLModel, Field, Column, Text

class CompanySettings(SQLModel, table=True):
    __tablename__ = "company_settings"

    id: int = Field(default=1, primary_key=True)
    
    # Brand Context
    marketing_strategy: Optional[str] = Field(default=None, sa_column=Column(Text))
    icp: Optional[str] = Field(default=None, sa_column=Column(Text))
    core_pillars: Optional[str] = Field(default=None, sa_column=Column(Text))
    tone_of_voice: Optional[str] = Field(default=None, sa_column=Column(Text))
    audiences: Optional[str] = Field(default=None, sa_column=Column(Text))
    company_description: Optional[str] = Field(default=None, sa_column=Column(Text))
    summarized_context: Optional[str] = Field(default=None, sa_column=Column(Text))

    # WordPress Credentials
    wp_site_url: Optional[str] = Field(default=None)
    wp_username: Optional[str] = Field(default=None)
    wp_app_password: Optional[str] = Field(default=None)

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
