import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from sqlalchemy import JSON, Text, Column, Boolean
from sqlmodel import SQLModel, Field


class JobStatus(str, Enum):
    queued = "queued"               # waiting in line behind other jobs
    pending = "pending"             # legacy / direct-start (treated same as queued)
    running = "running"
    resuming = "resuming"           # keyword confirmed; about to restart writing
    pending_review = "pending_review"  # keyword gate (current_step=keyword_confirmation)
                                       # OR content review gate (current_step=None)
    paused = "paused"               # manually paused by the user
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
    core_messaging_pillar: Optional[str] = Field(default=None)
    primary_keyword: Optional[str] = Field(default=None)
    secondary_keywords: List[str] = Field(default=[], sa_column=Column(JSON))
    evaluation_metrics: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    user_titles: List[str] = Field(default=[], sa_column=Column(JSON))
    competitor_urls: List[str] = Field(default=[], sa_column=Column(JSON))
    seed_keywords: List[str] = Field(default=[], sa_column=Column(JSON))
    personalization_snippets: Optional[str] = Field(default=None, sa_column=Column(Text))
    target_persona: Optional[str] = Field(default=None)

    # ── Queue & workflow control ─────────────────────────────────────────
    queue_position: Optional[int] = Field(default=None)   # 1 = next to run
    auto_approve: bool = Field(default=False, sa_column=Column(Boolean))  # skip all review gates

    # ── Workflow Settings ───────────────────────────────────────────────
    publish_targets: List[str] = Field(default=["wordpress", "linkedin"], sa_column=Column(JSON))
    publish_wordpress: bool = Field(default=True, sa_column=Column(Boolean))
    publish_linkedin: bool = Field(default=True, sa_column=Column(Boolean))
    publish_newsletter: bool = Field(default=False, sa_column=Column(Boolean))
    is_newsletter: bool = Field(default=False, sa_column=Column(Boolean))
    is_recurring: bool = Field(default=False, sa_column=Column(Boolean))
    recurring_interval: Optional[str] = Field(default=None) # 'weekly', 'monthly'
    recurring_day: Optional[str] = Field(default=None) # 'Monday', '1', '15'
    newsletter_timeframe: Optional[str] = Field(default=None) # e.g. 'week', 'month'
    newsletter_list_ids: List[int] = Field(default=[], sa_column=Column(JSON))
    scheduled_at: Optional[datetime] = Field(default=None)

    # ── Research output ──────────────────────────────────────────────────
    keyword_data: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    scraped_content: Optional[List[dict]] = Field(
        default=None, sa_column=Column(JSON)
    )
    # Keyword gate: SERP format + candidate snapshot shown to user for confirmation
    keyword_review_data: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # Keyword the user confirmed (may differ from AI-chosen keyword)
    confirmed_keyword: Optional[str] = Field(default=None)

    # ── Generated content ────────────────────────────────────────────────
    content_plan: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    article_markdown: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    linkedin_post: Optional[str] = Field(default=None, sa_column=Column(Text))
    newsletter_subject: Optional[str] = Field(default=None)
    newsletter_preheader: Optional[str] = Field(default=None)
    newsletter_html: Optional[str] = Field(default=None, sa_column=Column(Text))
    generated_images: List[str] = Field(default=[], sa_column=Column(JSON))
    selected_image: Optional[str] = Field(default=None)
    nano_banana_prompt: Optional[str] = Field(default=None, sa_column=Column(Text))

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
    gbp_post_name: Optional[str] = Field(default=None)
    newsletter_campaign_id: Optional[str] = Field(default=None)

    # ── Error tracking ───────────────────────────────────────────────────
    error_message: Optional[str] = Field(
        default=None, sa_column=Column(Text)
    )
    error_step: Optional[str] = Field(default=None)

    # ── Cluster Plan Link ────────────────────────────────────────────────
    cluster_plan_id: Optional[str] = Field(default=None, index=True)

    # ── Token usage ──────────────────────────────────────────────────────
    total_tokens_used: Optional[int] = Field(default=None)
    input_tokens_used: Optional[int] = Field(default=0)
    output_tokens_used: Optional[int] = Field(default=0)


class ClusterPlan(SQLModel, table=True):
    __tablename__ = "cluster_plans"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), primary_key=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    seed: str = Field(index=True)
    tasks: List[dict] = Field(default=[], sa_column=Column(JSON))
    approved: bool = Field(default=False)

    # Stateful multiagent content planning fields
    status: str = Field(default="planning")  # planning, keyword_review, generating_clusters, cluster_review, approved, failed
    current_step: Optional[str] = Field(default="keyword_research")  # keyword_research, strategy_generation, scheduling
    keywords: List[dict] = Field(default=[], sa_column=Column(JSON))
    num_pillars: int = Field(default=3)
    spokes_per_pillar: int = Field(default=3)
    publish_targets: List[str] = Field(default=["wordpress", "linkedin"], sa_column=Column(JSON))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    
    # Configurable keyword constraints
    min_search_volume: int = Field(default=50)
    max_search_volume: int = Field(default=1000)
    max_difficulty: int = Field(default=40)
    competitor_url: Optional[str] = Field(default=None)

    # Audience targeting split — e.g. [{"persona": "CTOs", "percentage": 40}, {"persona": "Developers", "percentage": 60}]
    audience_split: Optional[List[dict]] = Field(default=None, sa_column=Column(JSON))


