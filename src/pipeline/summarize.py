"""
Company Context Summarization Step
"""
import google.generativeai as genai

from src.config import settings

genai.configure(api_key=settings.gemini_api_key)

_PROMPT = """\
You are an expert AI Assistant configurer.
Your task is to take the raw company settings below and compress them into a dense, 
highly-optimized context string to be used as a System Prompt Memory.

## Raw Company Settings
{settings_json}

## Instructions
- Extract all core messaging, ICP definition, tone of voice, and strategies.
- Remove redundant words or fluff.
- Structure it as bullet points or brief paragraphs.
- Ensure no critical strategic detail or target audience is lost.
- This will be injected into future prompts to guide the AI's writing. Make it clear and authoritative.

Return ONLY the summarized context string. Do not wrap in markdown code blocks.
"""

async def summarize_company_context(settings_dict: dict) -> str:
    # Filter out empty values
    valid_settings = {k: v for k, v in settings_dict.items() if v and k != "id" and k != "summarized_context"}
    
    if not valid_settings:
        return ""
        
    prompt = _PROMPT.format(
        settings_json="\n".join([f"- {k.replace('_', ' ').title()}: {v}" for k, v in valid_settings.items()])
    )

    model = genai.GenerativeModel(
        settings.gemini_planning_model, # Flash model is perfect for summarization
        generation_config=genai.GenerationConfig(max_output_tokens=1024),
    )

    response = await model.generate_content_async(prompt)
    return response.text.strip()
