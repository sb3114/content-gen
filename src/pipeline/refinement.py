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
    updated_article: str = Field(description="The updated Markdown article.")
    updated_linkedin: str = Field(description="The updated LinkedIn post.")

_PROMPT = """\
You are an expert editor and SEO content strategist.
You are helping a user refine an existing blog article and its accompanying LinkedIn post based on their specific feedback.

{company_context_section}

## Current Article Draft
{article_draft}

## Current LinkedIn Post
{linkedin_draft}

## User Request / Feedback
{user_prompt}

## Instructions
- Determine if the user's feedback applies to the Article, the LinkedIn post, or both.
- Apply the user's feedback to the respective content.
- Do NOT rewrite the entire content if the user only asked for a minor change. Only change what is necessary to fulfill the request.
- Keep all formatting intact (H2, H3, bold text, lists).
- Ensure the tone remains authoritative and expert, unless the user explicitly requested a tone change.
- Return a valid JSON object matching the requested schema containing the updated texts. For the content you did NOT modify, return the exact original text.
"""

async def run_refinement(article_draft: str, linkedin_draft: str, user_prompt: str, company_context: str = "") -> tuple[dict, dict]:
    
    # Format company context if provided
    ctx_section = ""
    if company_context and company_context.strip():
        ctx_section = f"## Company Context (Ensure Revisions Align With This)\n{company_context}\n"

    prompt = _PROMPT.format(
        company_context_section=ctx_section,
        article_draft=article_draft,
        linkedin_draft=linkedin_draft,
        user_prompt=user_prompt
    )

    schema = _clean_schema(RefinedContent.model_json_schema())

    model = genai.GenerativeModel(
        settings.gemini_writing_model,
        generation_config=genai.GenerationConfig(
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )

    response = await model.generate_content_async(prompt)
    
    usage = {
        "in": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
        "out": response.usage_metadata.candidates_token_count if response.usage_metadata else 0
    }
    
    # Strip markdown code blocks if the model wrapped the JSON
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("\n", 1)[0]
            
    return json.loads(text), usage
