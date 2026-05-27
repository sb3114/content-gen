"""
Writing step: Gemini Pro → full article Markdown.
"""
import google.generativeai as genai

from src.config import settings
from src.schemas.content_plan import ContentPlan

genai.configure(api_key=settings.gemini_api_key)

_PROMPT = """\
Expert blog writer. Write an article from the plan below.

{company_context_section}

## Specs
Title: {title}
SEO: {focus_keyword}, {secondary_keywords}
Tone: {tone} | Count: {word_count}

## Plan
Outline: {outline}
Angles: {angles}

## Rules (GEO)
- Start with first H2 (No H1). 
- Answer intent directly in first paragraphs.
- Short paragraphs (2-4 sentences). 
- Scannable (bolding, lists).
- Expert tone. Clean Markdown.

Return Markdown body ONLY.
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

    from src.pipeline.llm import call_llm

    text, usage = await call_llm(
        prompt=prompt,
        tier="sonnet"
    )
    
    return text, usage
