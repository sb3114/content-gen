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
You are an email marketing expert. Create a concise, high-engagement digest newsletter of our recent publications and industry news.

## Period
{timeframe}

## Recent Published Blogs on BondNow
{articles_text}

## Newsletter Rules
- Subject line: Engaging hook summarizing the value of the digest (e.g., "The best of this week in Elderly Care & Tech").
- Preheader: A quick summary of the key insights waiting inside.
- Body HTML:
    - Brief, high-energy intro <h2>.
    - Summarise what has happened on BondNow based on the Recent Published Blogs provided. List each article with an <h3> title, a short engaging summary, and an <a> tag linking to the URL.
    - Seamlessly integrate the latest updates and news happening in healthcare, healthtech, elderly care, and independent living. **You MUST use Google Search to find recent, real news. Do not hallucinate or rely on internal memory.** Cite well-known, authoritative sites like the Dementia Society, AgeUK, Alzheimer's Association, or NHS.
    - Format nicely for ease of readability (use short paragraphs, bullet points <ul>/<li>).
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
        
        is_summary = job.is_newsletter or job.newsletter_type == "summary"
        
    if is_summary:
        timeframe = job.newsletter_timeframe or "week"
        days = 7 if timeframe == "week" else 30
        since = datetime.utcnow() - timedelta(days=days)
        
        from src.models.blog import PublishedBlog
        async with AsyncSessionLocal() as session:
            stmt = select(PublishedBlog).where(
                PublishedBlog.created_at >= since
            )
            recent_blogs = (await session.exec(stmt)).all()
            
        articles_text = ""
        for b in recent_blogs:
            articles_text += f"- Title: {b.title}\n  URL: {b.url}\n  Context: {b.description or b.context[:150]}\n\n"
            
        prompt = _SUMMARY_PROMPT.format(
            timeframe=f"Past {timeframe.capitalize()}",
            articles_text=articles_text or "No new articles published in this period."
        )
    else:
        # Standard update
        excerpt = " ".join(article_markdown.split()[:150]) if article_markdown else ""
        title = plan.chosen_title if plan else job.topic
        prompt = _UPDATE_PROMPT.format(
            title=title,
            target_audience=plan.target_audience if plan else "Subscribers",
            excerpt=excerpt,
            blog_url="[Insert Final URL Here]"
        )

    from src.pipeline.llm import call_llm
    response_text, usage = await call_llm(
        prompt=prompt,
        tier="sonnet",
        response_schema=NewsletterSchema,
        use_search_grounding=True
    )
    return NewsletterSchema(**json.loads(response_text)), usage
