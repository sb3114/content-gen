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
- CTA: end with a clear call to action
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

    model = genai.GenerativeModel(
        settings.gemini_planning_model,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=_pydantic_to_genai_schema(LinkedInPostSchema),
            max_output_tokens=4096,
        ),
    )

    response = await model.generate_content_async(prompt)
    
    usage = {
        "in": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
        "out": response.usage_metadata.candidates_token_count if response.usage_metadata else 0
    }
    
    # Strip markdown code blocks if present
    text = response.text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    
    return LinkedInPostSchema(**json.loads(text)), usage
