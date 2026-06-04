"""
Writing step: Gemini Pro → full article Markdown.
"""
import google.generativeai as genai

from src.config import settings
from src.schemas.content_plan import ContentPlan

genai.configure(api_key=settings.gemini_api_key)

_PROMPT = """\
You are an elite healthtech, elderly care, and healthcare copywriter. Write a highly detailed, professional, and engaging blog article based on the planning specifications below.

{company_context_section}
{personalization_section}
{paa_section}
{competitor_section}
{style_memory_section}

## Specifications
- **Title**: {title}
- **SEO Focus Keyword**: {focus_keyword}
- **Secondary Keywords/Tags**: {secondary_keywords}
- **Target Word Count**: {word_count}
- **Writing Tone**: {tone} (Expert, empathetic, and authoritative)

## Content Plan Structure
- **Outline**:
{outline}

- **Content Angles & Themes**:
{angles}

## Strict Writing Rules

1. **Information Accuracy & Sources**:
   - **DO NOT INVENT or hallucinate any information, claims, or facts.**
   - Base all medical, technological, and caregiving knowledge strictly on well-known healthcare, elderly care, dementia, and Alzheimer's institutions (e.g., Mayo Clinic, Alzheimer's Association, National Institute on Aging, WHO, NHS, ageuk.org.uk, brightmind.ai, alzheimers.org.uk, dementiaaction.org.uk, dementiashare.com, mind.org.uk)
2. **Data, Statistics & Verifiable Evidence**:
   - Use real, public statistics, numbers, data, and evidence. **NEVER invent, approximate, or fabricate any numbers or percentages.**
   - For every statistic, claim, or clinical guideline, you **MUST provide a clickable HTML hyperlink** to the authoritative public resource in question (e.g., `<a href="https://www.alz.org">Alzheimer's Association</a>` or `<a href="https://www.mayoclinic.org">Mayo Clinic</a>`). Ensure these links are formatted correctly as standard HTML `<a>` tags. Do NOT use markdown.

3. **SEO Foundations**:
   - Maintain excellent semantic keyword density naturally (no keyword stuffing).
   - Integrate the focus keyword naturally in the first H2, early in the introductory paragraph, and naturally across 2-3 other subheadings.
   - Secondary keywords must be seamlessly and naturally woven into the body text.
   - Maintain solid, authoritative text density and target high-intent keyword variations in introductory hooks.

4. **GEO (Generative Engine Optimization) Best Practices**:
   - **Inverted Pyramid Structure**: Always provide clear, concise, direct answers right at the beginning of each heading/section before expanding into contextual deep-dives.
   - **Machine-Readable Formats**: Structure heavy informational sections inside HTML tables or bulleted lists.
     - **Table Formatting**: Tables must have clearly defined columns, rows, and borders. Always style table headers (`<th>`) with inline styles setting the background-color to BondNow's brand color (`#7c3aed`), color to white, and font-family to 'Inter', sans-serif. Apply: `style="background-color: #7c3aed; color: #ffffff; font-family: 'Inter', sans-serif; padding: 10px; border: 1px solid #2a2a40; text-align: left;"`
     - Apply thin borders on table cells: `style="padding: 10px; border: 1px solid #2a2a40;"`
     - Apply table wrapper: `style="border-collapse: collapse; width: 100%; border: 1px solid #2a2a40; margin: 20px 0;"`
   - **People Also Ask Mapping**: Explicitly map relevant 'People Also Ask' questions (if provided in specifications above) into dedicated H2 or H3 question-and-answer pairs within the text to maximize visibility in AI search systems.
   - **Authority and Entity Clarity**: Maintain absolute objective, authoritative, entity-specific clarity. Minimize ambiguous pronouns. Write in an objective, clinical, fact-based tone.

5. **Style & Formatting**:
   - Write the entire article in raw HTML format. Start immediately with the first **H2** heading (do not write an H1 title). Do NOT include `<html>`, `<head>`, or `<body>` wrappers.
   - Directly answer the search intent in the very first paragraph.
   - Write in short, highly readable paragraphs (2-4 sentences max). **Never** write a paragraph containing more than 150 words.
   - Avoid starting three or more consecutive sentences with the exact same word. Mix sentence starters up to keep the flow organic and natural.
   - Make the text highly cohesive by using transition words/phrases (e.g., *however, therefore, in addition, consequently, furthermore, as a result, similarly*) naturally. Ensure at least 35-40% of the sentences contain transition words to optimize readability.
   - Keep vocabulary clear, simple, and suited for a broad, general audience. Avoid overly complex or academic words when simpler terms can express the same concept.
   - Make the text highly scannable using bolding (`<strong>`), bullet points (`<ul>` and `<li>`), and numbered lists (`<ol>` and `<li>`) formatted correctly in HTML.
   - Maintain an expert, warm, and highly professional tone throughout the article.

6. **Call to Action (CTA) & BondNow Integration**:
   - Subtly and naturally provide details on how **BondNow** can be used to help solve the specific issues being discussed in the article.
   - At the end of the article, you MUST add a relevant and compelling HTML Call to Action (CTA) link for BondNow based on the target audience:
     - If the target audience is families or general consumers: `<p><a href="https://bondnow.net/order/">Join BondNow</a> with our 2 months money-back guarantee today.</p>`
     - If the target audience is carehomes, homecarers, or professionals: `<p><a href="https://bondnow.net/pilot/">Join our pilot programme</a> to get started today.</p>`

Return the complete HTML body ONLY. Do NOT wrap the code in markdown code blocks like ```html.
"""


async def run_writing(
    plan: ContentPlan, 
    company_context: str = "", 
    personalization_snippets: str = "",
    people_also_ask: list[str] = None,
    competitor_urls: list[str] = None,
) -> tuple[str, dict]:
    outline_text = ""
    for section in plan.outline:
        outline_text += f"\n## {section.h2}  (intent: {section.intent})\n"
        for h3 in section.h3:
            outline_text += f"   ### {h3}\n"
        if section.key_points:
            outline_text += "   Key points: " + ", ".join(section.key_points) + "\n"

    # Load persistent brand context memory cache
    from src.pipeline.memory import load_brand_context_memory
    brand_ctx = load_brand_context_memory()

    # Format company context and brand voice parameters
    ctx_section = ""
    if company_context and company_context.strip():
        ctx_section = f"## Company Context (Write From This Perspective)\n{company_context}\n"
    elif brand_ctx.get("summarized_context"):
        ctx_section = f"## Company Context (Write From This Perspective)\n{brand_ctx['summarized_context']}\n"

    # Inject specific cached brand variables if present to reinforce brand limits
    if brand_ctx.get("company_description"):
        ctx_section += f"- **Company Bio**: {brand_ctx['company_description']}\n"
    if brand_ctx.get("marketing_strategy"):
        ctx_section += f"- **Marketing Strategy**: {brand_ctx['marketing_strategy']}\n"
    if brand_ctx.get("icp"):
        ctx_section += f"- **Target ICP**: {brand_ctx['icp']}\n"
    if brand_ctx.get("tone_of_voice"):
        ctx_section += f"- **Tone of Voice**: {brand_ctx['tone_of_voice']}\n"
    if ctx_section:
        ctx_section += "\n"

    # Format personalization snippets if provided
    pers_section = ""
    if personalization_snippets and personalization_snippets.strip():
        pers_section = f"## Real-World Personalization Stories, Snippets & Ideas\nYou MUST strictly integrate the following personalized stories, real anecdotes, pilot metrics, or core ideas into the article body. Weave them in naturally, vividly, and humanly to ground the content in authentic real-world experience, making it highly discoverable by Google's helpful content systems:\n{personalization_snippets}\n"

    # Format PAA section if provided
    paa_sec = ""
    if people_also_ask:
        paa_sec = "## People Also Ask (PAA) Questions to Integrate\nYou MUST map the following 'People Also Ask' questions into dedicated H2/H3 question-and-answer pairs within the article to maximize LLM search visibility:\n" + "\n".join(f"- {q}" for q in people_also_ask) + "\n\n"

    # Format competitor blog section if provided
    comp_section = ""
    if competitor_urls:
        valid_urls = [u for u in competitor_urls if u]
        if valid_urls:
            comp_section = "## Competitor Reference to Adapt\nWe identified the following similar competitor blog post(s)/pages during research:\n"
            for url in valid_urls:
                comp_section += f"- {url}\n"
            comp_section += "You MUST use these competitor articles as a structural reference. Understand their layout, depth, and main points, and adapt/re-engineer them to be superior, original, and beautifully tailored to our brand **BondNow** (https://bondnow.net) using our discovered keywords.\n\n"

    # Load persistent style memory guidelines (user's past writing style feedback & edits)
    from src.pipeline.memory import load_style_memory
    style_mem = load_style_memory()
    style_sec = ""
    if style_mem and style_mem.strip():
        style_sec = f"## User Writing Style Guidelines (Mistakes to Avoid)\nYou MUST strictly follow these writing style preferences and guidelines learned from the user's manual edits and direct feedback. Do NOT repeat any of these stylistic mistakes:\n{style_mem}\n\n"

    cached_tone = brand_ctx.get("tone_of_voice") or plan.tone or "Expert, empathetic, and authoritative"

    prompt = _PROMPT.format(
        company_context_section=ctx_section,
        personalization_section=pers_section,
        paa_section=paa_sec,
        competitor_section=comp_section,
        style_memory_section=style_sec,
        title=plan.chosen_title,
        focus_keyword=plan.focus_keyword,
        secondary_keywords=", ".join(plan.secondary_keywords),
        meta_description=plan.meta_description,
        target_audience=plan.target_audience,
        tone=cached_tone,
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
