"""
LinkedIn adaptation step: Gemini Flash → LinkedIn post JSON.
"""
import json

import google.generativeai as genai

from src.config import settings
from src.schemas.content_plan import ContentPlan, LinkedInPostSchema
from src.pipeline.planning import _pydantic_to_genai_schema

genai.configure(api_key=settings.gemini_api_key)

_PROMPT = """\
You are a LinkedIn content expert. Transform the blog article below into a
high-performing LinkedIn post.

## Article Title
{title}

## Target Audience
{target_audience}

## Article Excerpt (first ~600 words)
{excerpt}

## LinkedIn Post Rules
- Hook (line 1): bold statement, surprising insight, or provocative question
- Body: 3-5 short paragraphs — insights, not a summary; add YOUR perspective
- One idea per paragraph, blank lines between
- CTA: end with a clear call to action. The very last sentence of the post body (before hashtags) must be "Discover more in comments"
- Hashtags: 3-5 relevant tags at end
- Length: 1200-1500 characters total
- Tone: conversational, first-person

Return JSON with: hook, key_insights (list), cta, hashtags (list), full_text.
"""


async def run_linkedin_adaptation(
    plan: ContentPlan, article_markdown: str
) -> tuple[LinkedInPostSchema, dict]:
    excerpt = " ".join(article_markdown.split()[:600])

    prompt = _PROMPT.format(
        title=plan.chosen_title,
        target_audience=plan.target_audience,
        excerpt=excerpt,
    )

    from src.pipeline.llm import call_llm

    text, usage = await call_llm(
        prompt=prompt,
        tier="sonnet",
        response_schema=LinkedInPostSchema
    )
    
    post_obj = LinkedInPostSchema(**json.loads(text))
    
    # Ensure "Discover more in comments" is in the full_text
    lower_text = post_obj.full_text.lower()
    if "discover more in comments" not in lower_text and "discover more in commen" not in lower_text:
        # Find where hashtags start (usually they start with #)
        hashtag_idx = post_obj.full_text.find("#")
        if hashtag_idx != -1:
            before = post_obj.full_text[:hashtag_idx].rstrip()
            after = post_obj.full_text[hashtag_idx:]
            post_obj.full_text = f"{before}\n\nDiscover more in comments\n\n{after}"
        else:
            post_obj.full_text = f"{post_obj.full_text.rstrip()}\n\nDiscover more in comments"
            
    return post_obj, usage

