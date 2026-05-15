import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from sqlalchemy import JSON, Text, Column, Boolean
from sqlmodel import SQLModel, Field


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    pending_review = "pending_review"
    approved = "approved"
    scheduled = "scheduled"
    publishing = "publishing"
    published = "published"
    rejected = "rejected"
    failed = "failed"


class ArticleJob(SQLModel, table=True):
    __tablename__ = "article_jobs"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), primary_key=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    status: JobStatus = Field(default=JobStatus.pending)
    current_step: Optional[str] = Field(default=None)

    # ── Inputs ──────────────────────────────────────────────────────────
    topic: Optional[str] = Field(default=None)
    user_titles: List[str] = Field(default=[], sa_column=Column(JSON))
    competitor_urls: List[str] = Field(default=[], sa_column=Column(JSON))
    seed_keywords: List[str] = Field(default=[], sa_column=Column(JSON))

    # ── Workflow Settings ───────────────────────────────────────────────
    publish_targets: List[str] = Field(default=["wordpress", "linkedin"], sa_column=Column(JSON))
    publish_wordpress: bool = Field(default=True, sa_column=Column(Boolean))
    publish_linkedin: bool = Field(default=True, sa_column=Column(Boolean))
    publish_newsletter: bool = Field(default=False, sa_column=Column(Boolean))
    newsletter_type: Optional[str] = Field(default="update") # 'update' or 'summary'
    newsletter_timeframe: Optional[str] = Field(default=None) # e.g. 'week', 'month'
    newsletter_list_ids: List[int] = Field(default=[], sa_column=Column(JSON))
    scheduled_at: Optional[datetime] = Field(default=None)

    # ── Research output ──────────────────────────────────────────────────
    keyword_data: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    scraped_content: Optional[List[dict]] = Field(
        default=None, sa_column=Column(JSON)
    )

    # ── Generated content ────────────────────────────────────────────────
    content_plan: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    article_markdown: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    linkedin_post: Optional[str] = Field(default=None, sa_column=Column(Text))
    newsletter_subject: Optional[str] = Field(default=None)
    newsletter_preheader: Optional[str] = Field(default=None)
    newsletter_html: Optional[str] = Field(default=None, sa_column=Column(Text))

    # ── HITL: user-edited fields ─────────────────────────────────────────
    reviewed_title: Optional[str] = Field(default=None)
    reviewed_markdown: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    reviewed_linkedin: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    reviewed_newsletter_subject: Optional[str] = Field(default=None)
    reviewed_newsletter_preheader: Optional[str] = Field(default=None)
    reviewed_newsletter_html: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )

    # ── Refinement Chat History ──────────────────────────────────────────
    chat_history: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSON))

    # ── Publishing results ───────────────────────────────────────────────
    wp_post_url: Optional[str] = Field(default=None)
    wp_post_id: Optional[str] = Field(default=None)
    linkedin_post_id: Optional[str] = Field(default=None)
    newsletter_campaign_id: Optional[str] = Field(default=None)

    # ── Error tracking ───────────────────────────────────────────────────
    error_message: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    error_step: Optional[str] = Field(default=None)

    # ── Token usage ──────────────────────────────────────────────────────
    total_tokens_used: Optional[int] = Field(default=None)
    input_tokens_used: Optional[int] = Field(default=0)
    output_tokens_used: Optional[int] = Field(default=0)
