"""
Style and Learning Memory Feedback Loop
"""
import os
import logging
from src.pipeline.llm import call_llm

logger = logging.getLogger("style_memory")

MEMORY_PATH = "data/agent_memory/style_learning_memory.md"

def load_style_memory() -> str:
    """Reads the persistent user style memory file if it exists."""
    os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read style learning memory: {e}")
    return ""

async def record_style_feedback(feedback_text: str):
    """
    Analyzes direct chat prompt feedback from the user, extracts evergreen guidelines,
    and merges them into the style memory file.
    """
    if not feedback_text or len(feedback_text.strip()) < 4:
        return

    prompt = f"""\
You are an expert style analyzer and editor. The user has given direct feedback to an AI writing assistant:
"{feedback_text}"

Extract any general, evergreen style rules, phrasing preferences, tone constraints, or formatting patterns that should be applied to future articles. 
Do not include rules specific to one singular topic (like "talk about seniors' screens"). Focus on general stylistic rules (like "Keep introductions under 3 sentences", "Avoid promotional exclamation marks", "Use UK spellings").

Return ONLY a bulleted list of 1-2 concise rules. If the user feedback is not a general style rule (e.g. just asking for a specific link or specific typo fix), return nothing.
"""
    try:
        rules, _ = await call_llm(prompt=prompt, tier="haiku")
        rules = rules.strip()
        if rules and "-" in rules:
            await merge_and_save_rules(rules)
    except Exception as e:
        logger.error(f"Failed to record style feedback: {e}")

async def record_edit_feedback(original_text: str, edited_text: str, topic: str):
    """
    Compares the original AI-generated article with the user's manual edits,
    extracts stylistic/phrasing rules, and merges them into the style memory.
    """
    if not original_text or not edited_text:
        return
    
    # Trim texts to prevent token excess but capture key styles
    orig_snippet = original_text[:5000]
    edit_snippet = edited_text[:5000]
    
    if orig_snippet.strip() == edit_snippet.strip():
        return

    prompt = f"""\
You are an elite editorial auditor. A user has manually corrected/edited an AI-generated article about "{topic}".
Compare the original draft snippets with the user's final edited draft to understand their writing style, phrasing preferences, and corrections.

## Original AI Draft Snippet
```markdown
{orig_snippet}
```

## User's Edited Draft Snippet
```markdown
{edit_snippet}
```

## Task
Analyze the differences. Identify tone preferences, phrasing improvements, spellings (e.g. UK vs US), formatting adjustments (e.g., bullet lists vs tables, paragraph lengths), and grammar changes.
Extract 1-3 evergreen style rules or formatting guidelines that the AI should follow to write exactly like the user and avoid these mistakes in the future.
Be highly specific and actionable (e.g., 'Use UK spellings like "organisation" instead of "organization"', 'Avoid ending paragraphs with rhetorical questions', 'Use standard bolding for key nouns', 'Prefer short sentences under 15 words').

Return ONLY a bulleted list of 1-3 concise rules. Do not write introductory or concluding text.
"""
    try:
        rules, _ = await call_llm(prompt=prompt, tier="haiku")
        rules = rules.strip()
        if rules and "-" in rules:
            await merge_and_save_rules(rules)
    except Exception as e:
        logger.error(f"Failed to record edit feedback: {e}")

async def merge_and_save_rules(new_rules_text: str):
    """
    Deduplicates and merges new rules into the existing style memory file using a quick LLM call.
    """
    current_memory = load_style_memory()
    
    prompt = f"""\
You are an expert style librarian. Merge these newly discovered style rules into our existing user style guidelines, deduplicating them and keeping the list clean and organized.

## Existing Guidelines
{current_memory or 'None yet.'}

## New Discovered Rules
{new_rules_text}

## Instructions
- Combine overlapping rules.
- Keep the list highly readable and actionable.
- Limit the total list to a maximum of 15 high-impact, bulleted rules (prioritize the newest and most general rules).
- Return ONLY the merged, clean bulleted list. Do not write introductory or concluding text.
"""
    try:
        merged_text, _ = await call_llm(prompt=prompt, tier="haiku")
        merged_text = merged_text.strip()
        if merged_text:
            os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
            with open(MEMORY_PATH, "w", encoding="utf-8") as f:
                f.write(merged_text)
            logger.info("Successfully updated persistent user style memory.")
    except Exception as e:
        logger.error(f"Failed to merge and save rules: {e}")

IMAGE_MEMORY_PATH = "data/agent_memory/image_prompt_memory.md"

def load_image_memory() -> str:
    """Reads the persistent image prompt memory file if it exists."""
    os.makedirs(os.path.dirname(IMAGE_MEMORY_PATH), exist_ok=True)
    if os.path.exists(IMAGE_MEMORY_PATH):
        try:
            with open(IMAGE_MEMORY_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read image prompt memory: {e}")
    return ""

async def merge_and_save_image_rules(new_rules_text: str):
    """Deduplicates and merges new image prompt rules into existing memory."""
    current_memory = load_image_memory()
    prompt = f"""\
You are an expert AI Image Generation constraint manager. Merge these newly discovered guardrails into our existing image prompt guidelines.

## Existing Guidelines
{current_memory or 'None yet.'}

## New Discovered Rules
{new_rules_text}

## Instructions
- Combine overlapping rules.
- Keep the list highly readable and actionable, focused purely on prompt engineering constraints for image generation models.
- Limit to a maximum of 15 bulleted rules.
- Return ONLY the merged, clean bulleted list.
"""
    try:
        merged_text, _ = await call_llm(prompt=prompt, tier="haiku")
        merged_text = merged_text.strip()
        if merged_text:
            os.makedirs(os.path.dirname(IMAGE_MEMORY_PATH), exist_ok=True)
            with open(IMAGE_MEMORY_PATH, "w", encoding="utf-8") as f:
                f.write(merged_text)
            logger.info("Successfully updated persistent image prompt memory.")
    except Exception as e:
        logger.error(f"Failed to merge and save image rules: {e}")

async def record_image_prompt_feedback(original_prompt: str, edited_prompt: str):
    """
    Compares the original AI-generated image prompt with the user's manual edits
    and extracts new safety/style constraints.
    """
    if not original_prompt or not edited_prompt:
        return
    if original_prompt.strip() == edited_prompt.strip():
        return

    prompt = f"""\
You are an AI Safety and Image Prompt auditor. The user has manually edited an image generation prompt to bypass safety filters or improve style.
Compare the original prompt with the user's final edited prompt.

## Original AI Prompt
```text
{original_prompt}
```

## User's Edited Prompt
```text
{edited_prompt}
```

## Task
Identify why the user made these changes (e.g. removing specific brand names, removing specific ages or 'toddler' which triggers safety blocks, changing lighting/style).
Extract 1-3 evergreen negative/positive constraints that our image prompt generator should follow in the future.
Be specific and actionable (e.g. 'Never mention specific brands like Amazon Echo', 'Avoid asking for photorealistic toddlers to prevent safety blocks').

Return ONLY a bulleted list of 1-3 concise rules. Do not write introductory text.
"""
    try:
        rules, _ = await call_llm(prompt=prompt, tier="haiku")
        rules = rules.strip()
        if rules and "-" in rules:
            await merge_and_save_image_rules(rules)
    except Exception as e:
        logger.error(f"Failed to record image prompt feedback: {e}")

BRAND_MEMORY_PATH = "data/agent_memory/brand_context_memory.json"

def save_brand_context_memory(settings_obj) -> dict:
    """Caches the brand context settings in the persistent memory file."""
    import json
    os.makedirs(os.path.dirname(BRAND_MEMORY_PATH), exist_ok=True)

    # Prefer icp_context; fall back to legacy icp if unset
    icp_ctx = getattr(settings_obj, "icp_context", "") or ""
    if not icp_ctx.strip():
        icp_ctx = getattr(settings_obj, "icp", "") or ""

    data = {
        "marketing_strategy": getattr(settings_obj, "marketing_strategy", "") or "",
        "icp_context": icp_ctx,
        "core_pillars": getattr(settings_obj, "core_pillars", "") or "",
        "tone_of_voice": getattr(settings_obj, "tone_of_voice", "") or "",
        "company_description": getattr(settings_obj, "company_description", "") or "",
        "summarized_context": getattr(settings_obj, "summarized_context", "") or "",
    }
    try:
        with open(BRAND_MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        logger.info("Successfully updated cached brand context memory.")
    except Exception as e:
        logger.error(f"Failed to write brand context memory: {e}")
    return data

def load_brand_context_memory() -> dict:
    """Loads the brand context and tone of voice from the persistent memory file. Fallbacks to DB if missing."""
    import json
    os.makedirs(os.path.dirname(BRAND_MEMORY_PATH), exist_ok=True)
    if os.path.exists(BRAND_MEMORY_PATH):
        try:
            with open(BRAND_MEMORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read brand context memory cache: {e}")
            
    # Fallback to database query if cache is empty or corrupted
    try:
        import asyncio
        from src.database import AsyncSessionLocal
        from src.models.settings import CompanySettings

        async def _fetch():
            async with AsyncSessionLocal() as session:
                settings_obj = await session.get(CompanySettings, 1)
                if settings_obj:
                    return save_brand_context_memory(settings_obj)
            return {}

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _fetch())
                return future.result()
        else:
            return asyncio.run(_fetch())
    except Exception as e:
        logger.warning(f"Could not load fallback brand context from DB: {e}")
    return {}


async def record_failed_image_prompt(failed_prompt: str, error_message: str):
    """
    Analyzes an image generation prompt that triggered safety blocks or failed,
    extracts safety/guardrail rules, and merges them into the image prompt memory.
    """
    if not failed_prompt:
        return
        
    prompt = f"""\
You are an AI Safety and Image Prompt auditor. The following image generation prompt failed or was blocked by safety filters:
"{failed_prompt}"

Failure Context/Reason:
"{error_message}"

Analyze the prompt to identify what concept, wording, or phrasing likely triggered the safety classifier or caused the failure (e.g., specific age descriptors like 'elderly' or 'child', touching, sensitive context, or brand names).
Extract 1-2 evergreen guidelines/guardrails that our image prompt generator should follow in the future to avoid similar failures.
Be specific and actionable (e.g. 'Avoid mentioning specific ages; use general descriptors', 'Avoid describing close physical touching between characters').

Return ONLY a bulleted list of 1-2 concise rules. Do not write introductory text.
"""
    try:
        rules, _ = await call_llm(prompt=prompt, tier="haiku")
        rules = rules.strip()
        if rules and "-" in rules:
            await merge_and_save_image_rules(rules)
    except Exception as e:
        logger.error(f"Failed to record failed image prompt memory: {e}")
