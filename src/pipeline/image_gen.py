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
    
    response = None
    for model_name in models_to_try:
        try:
            logger.info(f"Generating 3 images using {model_name}...")
            response = client.models.generate_images(
                model=model_name,
                prompt=image_prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=3,
                    output_mime_type='image/jpeg',
                    aspect_ratio="1:1"
                )
            )
            break
        except Exception as e:
            logger.warning(f"Failed to generate images using {model_name}: {e}")
            
    if not response:
        raise ValueError("Image generation failed. Response is None.")
    if not getattr(response, 'generated_images', None):
        logger.error(f"Image generation failed. Response: {response}")
        raise ValueError("Image generation failed with all available Imagen models (empty generated_images).")
        
    saved_paths = []
    for idx, img in enumerate(response.generated_images):
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
