"""
Writing step: Gemini Pro → full article Markdown.
"""
import google.generativeai as genai

from src.config import settings
from src.schemas.content_plan import ContentPlan

genai.configure(api_key=settings.gemini_api_key)

_PROMPT = """\
You are an expert blog writer and SEO specialist.
Write a comprehensive, engaging article based on the content plan below.

{company_context_section}

## Title (H1 — do NOT include in body)
{title}

## SEO
Focus keyword: {focus_keyword}
Secondary keywords: {secondary_keywords}
Meta description: {meta_description}
Target audience: {target_audience}
Tone: {tone}
Word count target: {word_count} words

## Outline
{outline}

## Unique Content Angles
{angles}

## Rules (Generative Engine Optimization - GEO)
- Start with the first H2 (never repeat the H1)
- Answer the user's primary intent directly and concisely in the first few paragraphs.
- Use short paragraphs (2-4 sentences max) for high readability.
- Integrate keywords naturally — no stuffing. Use semantic variations.
- Use formatting (bolding key terms, bulleted lists) to make content highly scannable.
- Maintain an authoritative, expert tone. Cite specific facts or statistics if they were provided in the outline.
- End with a strong conclusion and CTA
- No placeholder text like [INSERT STAT] — write real content
- Format in clean Markdown (H2, H3, **bold**, bullet lists)

Return ONLY the article body in Markdown, starting from the first H2.
"""


async def run_writing(plan: ContentPlan, company_context: str = "") -> tuple[str, dict]:
    outline_text = ""
    for section in plan.outline:
        outline_text += f"\n## {section.h2}  (intent: {section.intent})\n"
        for h3 in section.h3:
            outline_text += f"   ### {h3}\n"
        if section.key_points:
            outline_text += "   Key points: " + ", ".join(section.key_points) + "\n"

    # Format company context if provided
    ctx_section = ""
    if company_context and company_context.strip():
        ctx_section = f"## Company Context (Write From This Perspective)\n{company_context}\n"

    prompt = _PROMPT.format(
        company_context_section=ctx_section,
        title=plan.chosen_title,
        focus_keyword=plan.focus_keyword,
        secondary_keywords=", ".join(plan.secondary_keywords),
        meta_description=plan.meta_description,
        target_audience=plan.target_audience,
        tone=plan.tone,
        word_count=plan.word_count_target,
        outline=outline_text,
        angles="\n".join(f"- {a}" for a in plan.content_angles),
    )

    model = genai.GenerativeModel(
        settings.gemini_writing_model,
        generation_config=genai.GenerationConfig(max_output_tokens=8192),
    )

    response = await model.generate_content_async(prompt)
    
    usage = {
        "in": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
        "out": response.usage_metadata.candidates_token_count if response.usage_metadata else 0
    }
    
    return response.text, usage
