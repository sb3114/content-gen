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
    
    prompt_generator_prompt = f"""You are an elite creative director and prompt engineer. Your job is to write a highly detailed, professional image generation prompt for the Imagen model.
The image will be used for a blog post and a LinkedIn post.

Article Details:
- Title: {title}
- Abstract: {body_markdown[:1500]}
"""

    if feedback:
        prompt_generator_prompt += f"""
## User Feedback / Refinement Request
The user did not like the previous images. You MUST strictly incorporate this feedback to adjust the image details, subjects, setting, style, or focus:
"{feedback}"
"""

    prompt_generator_prompt += """
Guidelines for the image generation prompt:
1. It must produce a high-quality photograph that is as close as possible to a real, human-like look.
2. The context must align with BondNow (a platform connecting families and elderly parents, senior care, warm family bonding, technology assisting care).
3. Specify warm lighting, high detail, realistic skin texture, natural smiles, and empathetic scenes.
4. Avoid any text, banners, overlay, or logos in the image.
5. Keep it descriptive (describing the subject, setting, style, camera angle, and color palette).

Return ONLY the final text-to-image prompt as plain text. Do not include any introductory text, notes, conversational filler, markdown formatting, or quotes.
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


async def generate_images_for_job(job, db_settings=None, feedback: Optional[str] = None) -> List[str]:
    """Generates 3 image candidates for a job and saves them to static storage."""
    client = get_genai_client(db_settings)
    
    title = job.reviewed_title or job.topic
    body = job.reviewed_markdown or job.article_markdown or ""
    
    image_prompt = await generate_image_prompt(title, body, db_settings, feedback)
    
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
            response = client.models.generate_image(
                model=model_name,
                prompt=image_prompt,
                config=types.GenerateImageConfig(
                    number_of_images=3,
                    output_mime_type='image/jpeg',
                    aspect_ratio="1:1"
                )
            )
            break
        except Exception as e:
            logger.warning(f"Failed to generate images using {model_name}: {e}")
            
    if not response or not response.generated_images:
        raise ValueError("Image generation failed with all available Imagen models.")
        
    saved_paths = []
    for idx, img in enumerate(response.generated_images):
        import uuid
        # Use a unique identifier to avoid browser caching issues when regenerating
        filename = f"{job.id}_{uuid.uuid4().hex[:8]}_{idx}.jpg"
        filepath = os.path.join(static_dir, filename)
        img.image.save(filepath)
        saved_paths.append(f"/static/generated_images/{filename}")
        logger.info(f"Saved generated image {idx} to {filepath}")
        
    return saved_paths
