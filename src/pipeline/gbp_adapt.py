"""
Google Business Profile adaptation step: Claude/Gemini → GBP post JSON.
"""
import json
import logging
from src.schemas.content_plan import GBPostSchema

logger = logging.getLogger(__name__)

_PROMPT = """\
You are a local SEO and Google Business Profile content expert. Transform the blog article below into a highly engaging, punchy Google Business Profile Update post.

## Article Title
{title}

## Target Audience
{target_audience}

## Article Excerpt (first ~600 words)
{excerpt}

## Google Business Profile Post Rules
- Summarize the article in 2 to 3 punchy, high-impact sentences.
- Outline: Hook + Key Benefit/Insight + Warm Invitation to read more.
- The tone MUST match the BondNow brand voice: warm, reassuring, helpful, family-oriented, and professional.
- Max Length: Strictly under 250 characters. Do not exceed this limit!
- Do not write any hashtags or links.

Return JSON with:
summary: "the generated 2-3 sentence update post under 250 characters"
"""

async def run_gbp_adaptation(
    title: str, target_audience: str, article_markdown: str
) -> tuple[GBPostSchema, dict]:
    excerpt = " ".join(article_markdown.split()[:600])

    prompt = _PROMPT.format(
        title=title,
        target_audience=target_audience,
        excerpt=excerpt,
    )

    from src.pipeline.llm import call_llm

    text, usage = await call_llm(
        prompt=prompt,
        tier="haiku",
        response_schema=GBPostSchema
    )
    
    return GBPostSchema(**json.loads(text)), usage
