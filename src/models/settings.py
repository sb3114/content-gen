from typing import Optional
from sqlmodel import SQLModel, Field, Column, Text

class CompanySettings(SQLModel, table=True):
    __tablename__ = "company_settings"

    id: int = Field(default=1, primary_key=True)
    marketing_strategy: Optional[str] = Field(default=None, sa_column=Column(Text))
    icp: Optional[str] = Field(default=None, sa_column=Column(Text))
    core_pillars: Optional[str] = Field(default=None, sa_column=Column(Text))
    tone_of_voice: Optional[str] = Field(default=None, sa_column=Column(Text))
    audiences: Optional[str] = Field(default=None, sa_column=Column(Text))
    company_description: Optional[str] = Field(default=None, sa_column=Column(Text))
    summarized_context: Optional[str] = Field(default=None, sa_column=Column(Text))
