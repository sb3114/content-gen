"""
Planning step: Gemini Flash → structured ContentPlan JSON.
"""
import json

import google.generativeai as genai

from src.config import settings
from src.schemas.content_plan import ContentPlan

genai.configure(api_key=settings.gemini_api_key)

_PROMPT = """\
Expert SEO strategist. Create a content plan for: {topic}

{company_context_section}

## Inputs
User Ideas: {user_titles}
Keywords: {keyword_data}
Competitors: {scraped_summary}

## Instructions
- Improve title (click-worthy)
- Select 1 focus + 5-8 secondary keywords. **Prioritize "affordable" keywords**: those with the best balance of high search volume and LOW competition scores.
- Outline (H2/H3) with intent. Use GEO: answer primary intent early, scannable hierarchy.
- Word count: 1500-2500. Tone: expert.
- 160-char meta desc.
- 3-5 unique angles.
{serp_format_section}
Return valid JSON (ContentPlan schema).
"""


def _clean_schema(schema: dict) -> dict:
    """
    Recursively remove keys that google-generativeai's proto Schema does not
    support (e.g. 'default', 'title'). Required when using Pydantic-generated
    JSON schemas with google-generativeai <= 0.8.x.
    """
    _UNSUPPORTED = {"default", "title", "additionalProperties"}
    if isinstance(schema, dict):
        # Handle 'anyOf' by taking the first non-null type
        if "anyOf" in schema:
            valid_options = [opt for opt in schema["anyOf"] if opt.get("type") != "null"]
            if valid_options:
                return _clean_schema(valid_options[0])
            
        return {
            k: _clean_schema(v)
            for k, v in schema.items()
            if k not in _UNSUPPORTED
        }
    if isinstance(schema, list):
        return [_clean_schema(i) for i in schema]
    return schema


def _pydantic_to_genai_schema(model_class) -> dict:
    """Convert a Pydantic model to a cleaned schema dict safe for genai."""
    raw = model_class.model_json_schema()
    # Inline $defs (Pydantic v2 puts nested models here)
    defs = raw.pop("$defs", {})
    def _resolve(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                return _resolve(defs.get(ref_name, obj))
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(i) for i in obj]
        return obj
    resolved = _resolve(raw)
    return _clean_schema(resolved)


async def run_planning(
    topic: str,
    user_titles: list[str],
    keyword_data: dict,
    scraped_content: list[dict],
    company_context: str = "",
    focus_keyword: str = "",
    serp_format: str = "",
) -> tuple[ContentPlan, dict]:
    # Compress competitor content (save tokens)
    summaries = []
    for item in scraped_content[:3]:
        if item.get("text"):
            excerpt = " ".join(item["text"].split()[:250])
            summaries.append({
                "url": item["url"],
                "title": item.get("title", ""),
                "excerpt": excerpt,
                "word_count": item.get("word_count", 0),
            })

    # Format company context if provided
    ctx_section = ""
    if company_context and company_context.strip():
        ctx_section = f"## Company Context (Base Your Plan On This)\n{company_context}\n"

    # SERP format injection
    serp_section = ""
    if serp_format:
        serp_section = f"\nCRITICAL FORMAT REQUIREMENT: Google is currently ranking **{serp_format}** posts for this keyword. Structure the outline to match this format.\n"

    prompt = _PROMPT.format(
        company_context_section=ctx_section,
        topic=topic,
        user_titles="\n".join(f"- {t}" for t in user_titles) or "None",
        keyword_data=json.dumps(keyword_data, indent=2),
        scraped_summary=json.dumps(summaries, indent=2),
        serp_format_section=serp_section,
    )

    if focus_keyword:
        prompt += f"\n\nCRITICAL SEO REQUIREMENT:\nYou MUST use '{focus_keyword}' exactly as the 'focus_keyword' field in the returned JSON. Base the article outline, angles, and title on ranking for this focus keyword."

    from src.pipeline.llm import call_llm

    # 1. Run core high-thinking planning outline (Sonnet)
    plan_text, usage_sonnet = await call_llm(
        prompt=prompt,
        tier="sonnet",
        response_schema=ContentPlan
    )
    
    plan = ContentPlan(**json.loads(plan_text))

    # 2. Run post-processing to generate/refine SEO Title, Meta Description, and Tag Categorization (Haiku)
    haiku_prompt = f"""\
You are an expert SEO copywriter and categorizer.
Based on the following content plan outline and focus keyword, generate the perfect click-worthy SEO title (under 60 characters), a compelling meta description (under 160 characters), and a list of 4-6 highly relevant blog tags/categories to classify the post.

Focus Keyword: {plan.focus_keyword or focus_keyword}
Outline:
{json.dumps(plan.outline or [], indent=2)}

Return a valid JSON object matching this exact structure:
{{
  "chosen_title": "compelling click-worthy SEO title",
  "meta_description": "high search-intent meta description",
  "tags": ["tag1", "tag2", "tag3", "tag4"]
}}
"""
    
    haiku_text, usage_haiku = await call_llm(
        prompt=haiku_prompt,
        tier="haiku",
        use_json=True
    )
    
    haiku_data = json.loads(haiku_text)
    
    # Enrich the Sonnet-generated ContentPlan with the Haiku-generated SEO and tags
    plan.chosen_title = haiku_data.get("chosen_title") or plan.chosen_title
    plan.meta_description = haiku_data.get("meta_description") or plan.meta_description
    plan.secondary_keywords = haiku_data.get("tags") or plan.secondary_keywords or []

    # Combine token usage
    usage = {
        "in": usage_sonnet["in"] + usage_haiku["in"],
        "out": usage_sonnet["out"] + usage_haiku["out"]
    }
    
    return plan, usage

