import os
import logging
from typing import List, Optional
from google import genai
from google.genai import types
from src.config import settings

logger = logging.getLogger(__name__)

def get_genai_client(db_settings=None):
    """Retrieve GenAI client configured with Settings or CompanySettings API key."""
    api_key = None
    if db_settings and db_settings.gemini_api_key:
        api_key = db_settings.gemini_api_key
    else:
        api_key = settings.gemini_api_key
        
    if not api_key:
        raise ValueError("Gemini API key is not configured. Please set it in settings.")
    return genai.Client(api_key=api_key)


async def generate_image_prompt(title: str, body_markdown: str, db_settings=None, feedback: Optional[str] = None) -> str:
    """Uses models/nano-banana-pro-preview to write a high-fidelity image prompt."""
    client = get_genai_client(db_settings)
    
    prompt_generator_prompt = f"""Write a concise, highly descriptive image generation prompt for a blog post.
Title: {title}
Abstract: {body_markdown[:1000]}
"""

    if feedback:
        prompt_generator_prompt += f"\nApply this user feedback strictly: \"{feedback}\"\n"

    from src.pipeline.memory import load_image_memory
    image_memory = load_image_memory()
    
    prompt_generator_prompt += f"""
Required Style: Photorealistic, warm lighting, natural, empathetic.
Context: BondNow (senior care, family bonding, technology).
Constraints: No text, no overlays, no logos.

## Learned Guardrails (Strictly enforce these)
{image_memory if image_memory else 'None'}

Output ONLY the final image prompt as plain text. No markdown, no quotes, no conversational filler.
"""
    try:
        logger.info("Calling models/nano-banana-pro-preview to generate image prompt...")
        response = client.models.generate_content(
            model='nano-banana-pro-preview',
            contents=prompt_generator_prompt,
        )
        prompt_text = response.text.strip()
        # Remove surrounding quotes if model outputs them
        if prompt_text.startswith('"') and prompt_text.endswith('"'):
            prompt_text = prompt_text[1:-1].strip()
        logger.info(f"Generated prompt: {prompt_text}")
        return prompt_text
    except Exception as e:
        logger.error(f"Failed to generate prompt using nano-banana-pro-preview: {e}")
        # Empathetic fallback prompt matching BondNow context
        fallback = f"A warm, high-quality, realistic photograph of an elderly grandmother smiling with her family, warm lighting, natural skin texture, human-like look, 8k resolution, relating to: {title}"
        if feedback:
            fallback += f", incorporating feedback: {feedback}"
        return fallback


async def generate_images_for_job(job, db_settings=None, prompt_override: Optional[str] = None) -> List[str]:
    """Generates 3 image candidates for a job and saves them to static storage."""
    client = get_genai_client(db_settings)
    
    title = job.reviewed_title or job.topic
    body = job.reviewed_markdown or job.article_markdown or ""
    
    if prompt_override:
        image_prompt = prompt_override
    elif getattr(job, "nano_banana_prompt", None):
        image_prompt = job.nano_banana_prompt
    else:
        image_prompt = await generate_image_prompt(title, body, db_settings)
        if job:
            job.nano_banana_prompt = image_prompt
    
    static_dir = "src/ui/static/generated_images"
    os.makedirs(static_dir, exist_ok=True)
    
    models_to_try = [
        'imagen-4.0-fast-generate-001',
        'imagen-4.0-generate-001',
    ]
    
    saved_images = []
    max_attempts = 6
    attempt = 0
    model_index = 0
    
    while len(saved_images) < 3 and attempt < max_attempts:
        model_name = models_to_try[model_index]
        needed = 3 - len(saved_images)
        logger.info(f"Attempt {attempt + 1}: Generating {needed} image(s) using {model_name}...")
        try:
            response = client.models.generate_image(
                model=model_name,
                prompt=image_prompt,
                config=types.GenerateImageConfig(
                    numberOfImages=needed,
                    outputMimeType='image/jpeg',
                    aspectRatio="1:1"
                )
            )
            if response and getattr(response, 'generated_images', None):
                new_images = response.generated_images
                logger.info(f"Model {model_name} successfully generated {len(new_images)} image(s).")
                saved_images.extend(new_images)
            else:
                logger.warning(f"Model {model_name} returned empty generated_images (potential safety block).")
                model_index = (model_index + 1) % len(models_to_try)
                
                # Feedback to system memory
                from src.pipeline.memory import record_failed_image_prompt
                import asyncio
                asyncio.create_task(record_failed_image_prompt(
                    image_prompt,
                    f"Model {model_name} returned empty generated_images (potential safety block)."
                ))
                
                # Automatically regenerate prompt
                logger.info("Automatically regenerating image prompt to bypass safety block...")
                feedback_instruction = (
                    f"The previous prompt was blocked by safety filters: '{image_prompt}'. "
                    "Write an alternative, safer, and more abstract prompt that does not trigger safety filters (e.g. avoid realistic human close-ups or physical touching if they caused issues)."
                )
                image_prompt = await generate_image_prompt(title, body, db_settings, feedback=feedback_instruction)
                if job:
                    job.nano_banana_prompt = image_prompt
        except Exception as e:
            logger.warning(f"Failed to generate images using {model_name}: {e}")
            model_index = (model_index + 1) % len(models_to_try)
            
            # Feedback to system memory
            from src.pipeline.memory import record_failed_image_prompt
            import asyncio
            asyncio.create_task(record_failed_image_prompt(
                image_prompt,
                f"Failed using model {model_name} with exception: {str(e)}"
            ))
            
            # Automatically regenerate prompt if safety/policy related
            err_str = str(e).lower()
            if any(term in err_str for term in ["safety", "block", "policy", "content", "filter"]):
                logger.info("Automatically regenerating image prompt to bypass safety block...")
                feedback_instruction = (
                    f"The previous prompt was blocked by safety filters: '{image_prompt}'. "
                    "Write an alternative, safer, and more abstract prompt that does not trigger safety filters."
                )
                image_prompt = await generate_image_prompt(title, body, db_settings, feedback=feedback_instruction)
                if job:
                    job.nano_banana_prompt = image_prompt
            
        attempt += 1
        if len(saved_images) < 3 and attempt < max_attempts:
            import asyncio
            wait_time = 2 ** (attempt - 1)
            logger.info(f"Sleeping for {wait_time}s before next attempt...")
            await asyncio.sleep(wait_time)
            
    if not saved_images:
        raise ValueError("Image generation failed with all available Imagen models (empty generated_images).")
        
    saved_paths = []
    for idx, img in enumerate(saved_images):
        import uuid
        # Use a unique identifier to avoid browser caching issues when regenerating
        filename = f"{job.id}_{uuid.uuid4().hex[:8]}_{idx}.jpg"
        filepath = os.path.join(static_dir, filename)
        
        # In SDK 2.8.0, use image.image_bytes
        if hasattr(img.image, 'image_bytes'):
            with open(filepath, 'wb') as f:
                f.write(img.image.image_bytes)
        else:
            # Fallback
            img.image.save(filepath)
        saved_paths.append(f"/static/generated_images/{filename}")
        logger.info(f"Saved generated image {idx} to {filepath}")
        
    return saved_paths
