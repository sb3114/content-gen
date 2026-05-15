from pydantic import BaseModel
from typing import List, Optional


class OutlineSection(BaseModel):
    h2: str
    h3: List[str] = []
    intent: str
    key_points: List[str] = []


class ContentPlan(BaseModel):
    chosen_title: str
    focus_keyword: str
    secondary_keywords: List[str]
    outline: List[OutlineSection]
    tone: str
    meta_description: str
    word_count_target: int
    content_angles: List[str]
    target_audience: str


class LinkedInPostSchema(BaseModel):
    hook: str
    key_insights: List[str]
    cta: str
    hashtags: List[str]
    full_text: str


class NewsletterSchema(BaseModel):
    subject: str
    preheader: str
    greeting: str
    body_html: str
    cta_text: str
    cta_url: str = ""
