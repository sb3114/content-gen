"""
Company Context Summarization Step
"""
import google.generativeai as genai

from src.config import settings

genai.configure(api_key=settings.gemini_api_key)

_PROMPT = """\
Compress these company settings into a dense, bulleted "System Memory" string.
Remove fluff. Keep core messaging, ICP, tone, and strategy. 

## Settings
{settings_json}

Return ONLY the compressed text. No markdown.
"""

async def summarize_company_context(settings_dict: dict) -> str:
    # Filter out empty values
    valid_settings = {k: v for k, v in settings_dict.items() if v and k != "id" and k != "summarized_context"}
    
    if not valid_settings:
        return ""
        
    prompt = _PROMPT.format(
        settings_json="\n".join([f"- {k.replace('_', ' ').title()}: {v}" for k, v in valid_settings.items()])
    )

    from src.pipeline.llm import call_llm
    
    text, _ = await call_llm(
        prompt=prompt,
        tier="haiku"
    )
    return text.strip()


async def get_published_memory() -> str:
    """Fetch history of published articles for cross-linking."""
    from src.database import AsyncSessionLocal
    from src.models.job import ArticleJob, JobStatus
    from sqlmodel import select
    
    async with AsyncSessionLocal() as session:
        stmt = select(ArticleJob).where(ArticleJob.status == JobStatus.published)
        jobs = (await session.exec(stmt)).all()
        
    if not jobs:
        return ""
        
    memory = "\n## Published Article History (Use for Cross-linking & SEO)\n"
    for j in jobs:
        title = j.reviewed_title or j.topic
        if j.publish_wordpress and j.wp_post_url:
            memory += f"- Title: {title} | Target: WordPress | URL: {j.wp_post_url}\n"
        elif j.publish_linkedin:
            memory += f"- Title: {title} | Target: LinkedIn\n"
            
    return memory
