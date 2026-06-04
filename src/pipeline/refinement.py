"""
Refinement step: Gemini Pro -> updated Markdown and LinkedIn drafts.
"""
import json
import google.generativeai as genai
from pydantic import BaseModel, Field

from src.config import settings
from src.pipeline.planning import _clean_schema

genai.configure(api_key=settings.gemini_api_key)

class RefinedContent(BaseModel):
    updated_article: str = Field(description="The updated HTML article.")
    updated_linkedin: str = Field(description="The updated LinkedIn post.")

_PROMPT = """\
You are an expert editor and SEO content strategist.
You are helping a user refine an existing blog article and its accompanying LinkedIn post based on their specific feedback.

{company_context_section}
{style_memory_section}

## Current Article Draft (HTML format)
{article_draft}

## Current LinkedIn Post
{linkedin_draft}

## User Request / Feedback
{user_prompt}

## Instructions
- Determine if the user's feedback applies to the Article, the LinkedIn post, or both.
- Apply the user's feedback to the respective content.
- Do NOT rewrite the entire content if the user only asked for a minor change. Only change what is necessary to fulfill the request.
- Keep all formatting intact. Note that the article is written in raw HTML format. Ensure all tables with inline styles (`style="background-color: #7c3aed; ..."`), bullet points (`<ul>`, `<li>`), links (`<a>`), and CTAs remain correct HTML.
- Ensure the tone remains authoritative and expert, unless the user explicitly requested a tone change.
- Return a valid JSON object matching the requested schema containing the updated texts. For the content you did NOT modify, return the exact original text.
"""

async def run_refinement(article_draft: str, linkedin_draft: str, user_prompt: str, company_context: str = "") -> tuple[dict, dict]:
    
    # Format company context if provided
    ctx_section = ""
    if company_context and company_context.strip():
        ctx_section = f"## Company Context (Ensure Revisions Align With This)\n{company_context}\n"

    # Load persistent style memory guidelines
    from src.pipeline.memory import load_style_memory
    style_mem = load_style_memory()
    style_sec = ""
    if style_mem and style_mem.strip():
        style_sec = f"## User Writing Style Guidelines (Mistakes to Avoid)\nYou MUST strictly follow these writing style preferences and guidelines learned from the user's manual edits and direct feedback. Do NOT repeat any of these stylistic mistakes:\n{style_mem}\n\n"

    prompt = _PROMPT.format(
        company_context_section=ctx_section,
        style_memory_section=style_sec,
        article_draft=article_draft,
        linkedin_draft=linkedin_draft,
        user_prompt=user_prompt
    )

    schema = _clean_schema(RefinedContent.model_json_schema())

    from src.pipeline.llm import call_llm

    text, usage = await call_llm(
        prompt=prompt,
        tier="haiku",
        response_schema=RefinedContent
    )
    
    return json.loads(text), usage
