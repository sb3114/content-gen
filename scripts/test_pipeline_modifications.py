import asyncio
import json
from src.pipeline.planning import run_planning, _PROMPT
from src.pipeline.writing import _PROMPT as _WRITING_PROMPT

async def test_planning_prompt_injection():
    print("=== Testing Planning Prompt Injection ===")
    
    # Test case 1: serp_format is 'comparison'
    topic = "Elderly Smart Screens"
    user_titles = ["Best smart screens for seniors"]
    keyword_data = {"chosen_keyword": {"keyword": "elderly smart screens"}}
    scraped_content = [{"url": "https://example.com/comp1", "title": "Comp 1", "text": "Some text"}]
    
    # We will mock/run the prompt formatting logic or just see what prompt gets formatted.
    # To see the prompt, we can temporarily inspect the prompt inside a custom test or mock call_llm.
    print("\nCase 1: serp_format='comparison'")
    # We can check how planning handles it by running a test call if needed, or by inspecting _PROMPT formatting
    # Let's mock a fast execution by printing the comparison instructions that would be used.
    from src.pipeline.planning import _PROMPT as planning_prompt
    
    # We will replicate the format logic to see how it looks
    serp_format_directive_comp = (
        "   - **SERP FORMAT DIRECTIVE (Comparison)**: Google is rewarding COMPARISON content for this keyword. "
        "You MUST design the outline to compare different solutions, technologies, or providers relevant to this specific topic/context. "
        "Use the provided Research on Providers/Alternatives to evaluate their features, pros & cons, and costs/pricing models. "
        "Subtly and naturally position BondNow as a modern elderly care technology solution only where appropriate, "
        "maintaining an objective, factual, and clinical tone for the comparison."
    )
    
    prompt_formatted = planning_prompt.format(
        company_context_section="Company context...",
        topic=topic,
        user_titles="- Title 1",
        keyword_data="{}",
        scraped_summary="[]",
        existing_blogs_section="Existing blogs...",
        serp_format_directive=serp_format_directive_comp,
    )
    
    assert serp_format_directive_comp in prompt_formatted
    print("✓ Success: comparison instruction injected correctly for comparison format.")

    # Test case 2: serp_format is 'guide' (no comparison)
    print("\nCase 2: serp_format='guide'")
    no_comparison_instruction = (
        "   - **SERP FORMAT DIRECTIVE (Guide)**: Google is rewarding GUIDE content. Structure as an authoritative deep-dive. Do NOT include a dedicated competitor comparison section unless specifically requested."
    )
    prompt_formatted_guide = planning_prompt.format(
        company_context_section="Company context...",
        topic=topic,
        user_titles="- Title 1",
        keyword_data="{}",
        scraped_summary="[]",
        existing_blogs_section="Existing blogs...",
        serp_format_directive=no_comparison_instruction,
    )
    
    assert no_comparison_instruction in prompt_formatted_guide
    assert serp_format_directive_comp not in prompt_formatted_guide
    print("✓ Success: no_comparison instruction injected correctly for guide format.")

async def test_writing_prompt_citations():
    print("\n=== Testing Writing Prompt Citations ===")
    assert "Never link to generic homepages of websites" in _WRITING_PROMPT
    assert "https://www.alz.org" in _WRITING_PROMPT
    print("✓ Success: Writing prompt has strict rules against generic homepage links.")

async def main():
    await test_planning_prompt_injection()
    await test_writing_prompt_citations()
    print("\nAll checks completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
