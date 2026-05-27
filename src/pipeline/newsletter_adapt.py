"""
Newsletter adaptation step: Gemini Flash → Newsletter JSON.
"""
import json
from typing import Optional
import google.generativeai as genai
from datetime import datetime, timedelta
from sqlmodel import select

from src.config import settings
from src.schemas.content_plan import ContentPlan, NewsletterSchema
from src.pipeline.planning import _pydantic_to_genai_schema
from src.database import AsyncSessionLocal
from src.models.job import ArticleJob, JobStatus

genai.configure(api_key=settings.gemini_api_key)

_UPDATE_PROMPT = """\
You are an email marketing expert. Create a compelling, high-engagement newsletter based on the blog article below.

## Article Title
{title}

## Target Audience
{target_audience}

## Article Excerpt
{excerpt}

## Blog Article URL
{blog_url}

## Newsletter Rules
- Subject line: Engaging with a powerful hook based on the topic. Aim for curiosity or value.
- Preheader: Concise 1-sentence teaser that complements the subject.
- Body HTML:
    - Keep the entire email extremely concise (scannable in < 30 seconds).
    - Use <h2> for the main headline and <h3> for sub-headers.
    - Use <p> for short, punchy paragraphs.
    - **Crucial**: Include <a> tags with clear hyperlinks to the blog post URL (if provided) or high-quality public news resources.
    - Focus on a single "hook" that drives clicks to the full article.
- CTA: Direct, high-contrast, and inviting.


Return JSON with: subject, preheader, greeting, body_html, cta_text, cta_url.
"""

_SUMMARY_PROMPT = """\
You are an email marketing expert. Create a concise, high-engagement digest newsletter of our recent publications.

## Period
{timeframe}

## Recent Articles
{articles_text}

## Newsletter Rules
- Subject line: Engaging hook summarizing the value of the digest (e.g., "The best of this week in [Topic]").
- Preheader: A quick summary of the key insights waiting inside.
- Body HTML:
    - Brief, high-energy intro <h2>.
    - List each article with an <h3> title and a 1-sentence "why you should read this" hook.
    - Use <a> tags to link directly to the articles.
    - Use <ul> and <li> for scannability.
    - Aim for brevity and a high "click-to-read" intent.

Return JSON with: subject, preheader, greeting, body_html, cta_text, cta_url.
"""

async def run_newsletter_adaptation(
    job_id: str, 
    plan: Optional[ContentPlan] = None, 
    article_markdown: str = ""
) -> tuple[NewsletterSchema, dict]:
    async with AsyncSessionLocal() as session:
        job = await session.get(ArticleJob, job_id)
        if not job:
            raise ValueError("Job not found")
        
        newsletter_type = job.newsletter_type or "update"
        
    if newsletter_type == "summary":
        # Fetch recently published articles
        timeframe = job.newsletter_timeframe or "week"
        days = 7 if timeframe == "week" else 30
        since = datetime.utcnow() - timedelta(days=days)
        
        async with AsyncSessionLocal() as session:
            stmt = select(ArticleJob).where(
                ArticleJob.status == JobStatus.published,
                ArticleJob.updated_at >= since
            )
            recent_jobs = (await session.exec(stmt)).all()
            
        articles_text = ""
        for rj in recent_jobs:
            title = rj.reviewed_title or (rj.content_plan.get("chosen_title") if rj.content_plan else rj.topic)
            articles_text += f"- {title}: {rj.wp_post_url}\n"
            
        prompt = _SUMMARY_PROMPT.format(
            timeframe=timeframe,
            articles_text=articles_text or "No new articles this period."
        )
    else:
        # News Update type
        excerpt = " ".join(article_markdown.split()[:500]) if article_markdown else ""
        prompt = _UPDATE_PROMPT.format(
            title=plan.chosen_title if plan else job.topic,
            target_audience=plan.target_audience if plan else "Subscribers",
            excerpt=excerpt,
            blog_url=job.wp_post_url or ""
        )

    from src.pipeline.llm import call_llm

    text, usage = await call_llm(
        prompt=prompt,
        tier="sonnet",
        response_schema=NewsletterSchema
    )
    
    return NewsletterSchema(**json.loads(text.strip())), usage
