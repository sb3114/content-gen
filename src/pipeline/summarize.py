"""
Company Context Summarization Step.

Design philosophy:
- icp_context is NEVER compressed or paraphrased — it is preserved verbatim in the
  memory file as a dedicated section so the orchestrator can read it directly.
- Other brand fields (strategy, pillars, tone) are compressed into a dense
  "System Memory" prefix so they don't bloat every LLM prompt.
- The final summarized_context written to DB is structured Markdown so downstream
  code can predictably extract sections.
"""
import logging
from src.config import settings

logger = logging.getLogger(__name__)

# Fields to compress (exclude raw ICP — preserved separately)
_COMPRESS_KEYS = {
    "company_description": "Company Description",
    "marketing_strategy": "Marketing Strategy",
    "core_pillars": "Core Content Pillars",
    "tone_of_voice": "Tone of Voice",
    "audiences": "Target Audiences",
}

_COMPRESS_PROMPT = """\
You are a senior brand strategist building an AI system memory.

Compress the following brand settings into a dense, structured summary.
RULES:
- Preserve ALL key facts, angles, differentiators, product names, and audience language.
- Do NOT dilute, paraphrase, or drop specific details.
- Use tight bullet points grouped under bold headers.
- Do NOT include the ICP / Personas / Pain Points section — that is handled separately.
- Output clean Markdown. No preamble.

## Brand Settings
{settings_text}
"""


async def summarize_company_context(settings_dict: dict) -> str:
    """
    Produces a structured Markdown memory block.

    Structure:
      ## Brand Strategy Memory
      <compressed brand/strategy bullets>

      ## ICP & Persona Context (verbatim)
      <icp_context pasted as-is>
    """
    from src.pipeline.llm import call_llm

    # 1. Compress the non-ICP brand fields
    compress_parts = []
    for key, label in _COMPRESS_KEYS.items():
        val = settings_dict.get(key) or ""
        if val.strip():
            compress_parts.append(f"**{label}**: {val.strip()}")

    compressed_brand = ""
    if compress_parts:
        try:
            text, _ = await call_llm(
                prompt=_COMPRESS_PROMPT.format(settings_text="\n".join(compress_parts)),
                tier="haiku",
            )
            compressed_brand = text.strip()
        except Exception as e:
            logger.warning(f"Brand compression LLM call failed, using raw: {e}")
            compressed_brand = "\n".join(compress_parts)

    # 2. Preserve ICP context verbatim — migrate legacy icp if icp_context is empty
    icp_context = (settings_dict.get("icp_context") or "").strip()
    if not icp_context:
        # Fall back to legacy icp field
        icp_context = (settings_dict.get("icp") or "").strip()

    # 3. Assemble structured memory block
    sections = []
    if compressed_brand:
        sections.append(f"## Brand Strategy Memory\n{compressed_brand}")
    if icp_context:
        sections.append(f"## ICP & Persona Context\n{icp_context}")

    return "\n\n".join(sections)


async def get_published_memory() -> str:
    """Fetch history of published articles for cross-linking."""
    from src.database import AsyncSessionLocal
    from src.models.job import ArticleJob, JobStatus
    from sqlmodel import select

    async with AsyncSessionLocal() as session:
        stmt = select(ArticleJob).where(ArticleJob.status == JobStatus.published)
        jobs = (await session.exec(stmt)).all()

    if not jobs:
        return ""

    memory = "\n## Published Article History (Use for Cross-linking & SEO)\n"
    for j in jobs:
        title = j.reviewed_title or j.topic
        if j.publish_wordpress and j.wp_post_url:
            memory += f"- Title: {title} | Target: WordPress | URL: {j.wp_post_url}\n"
        elif j.publish_linkedin:
            memory += f"- Title: {title} | Target: LinkedIn\n"

    return memory
