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

    model = genai.GenerativeModel(
        settings.gemini_planning_model,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=_pydantic_to_genai_schema(ContentPlan),
            max_output_tokens=8192,
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
    
    return ContentPlan(**json.loads(text)), usage
