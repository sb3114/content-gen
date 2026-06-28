import json
import logging
import os
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

class LLMRateLimitException(Exception):
    """Raised when an LLM provider rate limit is encountered."""
    def __init__(self, message: str, retry_after_seconds: int = 600):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


async def call_llm(
    prompt: str,
    tier: str = "sonnet",
    system_instruction: str = None,
    response_schema = None,
    use_json: bool = False,
    db_settings = None,
    use_search_grounding: bool = False
) -> tuple[str, dict]:
    """
    Centralized LLM router function.
    Routes requests to Claude CLI (if configured) or Google Gemini API.
    
    Tiers:
      - "opus": Claude 3 Opus (complex reasoning/planning)
      - "sonnet": Claude 3.5 Sonnet or Gemini 2.5 Pro (complex tasks/writing)
      - "haiku": Claude 3.5 Haiku or Gemini 2.0 Flash (validation, summary, tags, metadata)
    """
    from src.database import AsyncSessionLocal
    from src.models.settings import CompanySettings

    if not db_settings:
        async with AsyncSessionLocal() as session:
            db_settings = await session.get(CompanySettings, 1)
            
    provider = db_settings.llm_provider if db_settings else "gemini"

    if provider == "claude":
        token = db_settings.claude_setup_token if db_settings else None
        if not token:
            raise ValueError("Claude Setup Token is not configured in Settings. Please save a setup-token or switch to Gemini.")

        # Determine target model
        if tier == "opus":
            model = "opus"
        elif tier == "sonnet":
            model = "sonnet"
        else:
            model = "haiku"

        # Inner helper to invoke Claude CLI asynchronously
        async def _run_claude(model_name: str) -> tuple[str, dict]:
            # Format system instruction and JSON prompts
            full_prompt = ""
            if system_instruction:
                full_prompt += f"System Instruction:\n{system_instruction}\n\n"
            full_prompt += prompt

            if response_schema or use_json:
                schema_desc = ""
                if response_schema:
                    if hasattr(response_schema, "model_json_schema"):
                        schema_desc = json.dumps(response_schema.model_json_schema())
                    else:
                        schema_desc = json.dumps(response_schema)
                
                full_prompt += (
                     "\n\nCRITICAL: You MUST return a valid JSON object. "
                     "Do NOT wrap the JSON in ```json code blocks or markdown formatting. "
                     "Do not include any introductory or concluding text. Return ONLY the raw JSON string.\n"
                )
                if schema_desc:
                    full_prompt += f"The JSON must strictly conform to this schema:\n{schema_desc}\n"

            # Execute CLI in headless bare mode
            env = os.environ.copy()
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            
            # Native headless authentication is automatically handled by exporting CLAUDE_CODE_OAUTH_TOKEN
            # in env, avoiding any blocking or interactive prompts.

            cmd = ["claude", "-p", full_prompt, "--model", model_name]
            
            if use_search_grounding:
                cmd.extend(["--chrome", "--permission-mode", "auto"])
                
            logger.info(f"Invoking Claude CLI asynchronously: {model_name} with args: {cmd}")
            import asyncio
            proc = None
            try:
                # Start the subprocess asynchronously
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env
                )
                # Wait for the subprocess — 360s to handle large strategy prompts
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=360
                )
                stdout = stdout_bytes.decode(errors="replace")
                stderr = stderr_bytes.decode(errors="replace")
                returncode = proc.returncode
            except asyncio.TimeoutError:
                # Kill the orphaned subprocess so it doesn't keep consuming resources
                if proc and proc.returncode is None:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
                raise LLMRateLimitException(
                    f"Claude CLI request timed out (model: {model_name}) after 360 seconds. Will retry shortly.",
                    retry_after_seconds=120
                )
            except Exception as subprocess_err:
                raise ValueError(f"Failed to execute Claude CLI: {subprocess_err}")

            # Strip ANSI escape color codes from output
            def strip_ansi(text: str) -> str:
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                return ansi_escape.sub('', text)

            stdout = strip_ansi(stdout or "")
            stderr = strip_ansi(stderr or "")
            err_text = stdout + "\n" + stderr

            # Catch Rate Limits (429, Too Many Requests, Quota hit)
            is_rate_limit = any(kw in err_text.lower() for kw in ["rate limit", "rate-limit", "quota exceeded", "429", "too many requests"])
            if is_rate_limit:
                retry_after = 600  # default to 10 minutes
                
                # Check for reset timers in output
                m = re.search(r"try again in (\d+)m", err_text, re.IGNORECASE)
                if m:
                    retry_after = int(m.group(1)) * 60
                else:
                    m2 = re.search(r"retry in (\d+) seconds", err_text, re.IGNORECASE)
                    if m2:
                        retry_after = int(m2.group(1))

                raise LLMRateLimitException(
                    f"Claude CLI rate limit reached (model: {model_name}). Output: {err_text}",
                    retry_after_seconds=retry_after
                )

            if returncode != 0:
                raise ValueError(f"Claude CLI failed with exit code {returncode}. Stderr: {stderr or stdout}")

            # Character-based token estimation for dashboard statistics
            in_tokens = len(full_prompt) // 4
            out_tokens = len(stdout) // 4
            usage = {"in": in_tokens, "out": out_tokens}

            text = stdout.strip()
            # Clean JSON formatting wrap if present
            if response_schema or use_json:
                match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
                if match:
                    text = match.group(1).strip()
                else:
                    # Fallback to finding first { and last }
                    start_idx = text.find("{")
                    end_idx = text.rfind("}")
                    if start_idx != -1 and end_idx != -1:
                        text = text[start_idx:end_idx+1]
                text = text.strip()

            return text, usage

        try:
            return await _run_claude(model)
        except LLMRateLimitException as exc:
            # Check if fallback is enabled
            allow_fallback = db_settings.allow_fallback_to_haiku if db_settings else True
            
            if tier == "opus" and allow_fallback:
                logger.warning(f"Claude Opus rate limit hit. Falling back to Sonnet: {exc}")
                try:
                    return await _run_claude("sonnet")
                except LLMRateLimitException as exc2:
                    logger.warning(f"Claude Sonnet rate limit hit during fallback. Falling back to Haiku: {exc2}")
                    return await _run_claude("haiku")
                    
            elif tier == "sonnet" and allow_fallback:
                logger.warning(f"Claude Sonnet rate limit hit. Falling back to Haiku: {exc}")
                return await _run_claude("haiku")
            else:
                raise

    else:
        # Standard Gemini execution using google-genai
        from google import genai
        from google.genai import types
        import hashlib
        from src.config import settings

        client = genai.Client(api_key=settings.gemini_api_key)

        gemini_model = settings.gemini_writing_model if tier == "sonnet" else settings.gemini_planning_model
        logger.info(f"Invoking Google Gemini: {gemini_model}")

        gen_config = {}
        if response_schema or use_json:
            gen_config["response_mime_type"] = "application/json"
            if response_schema:
                from src.pipeline.planning import _pydantic_to_genai_schema
                schema_dict = _pydantic_to_genai_schema(response_schema)
                gen_config["response_schema"] = schema_dict

        # Global dict for Gemini cache
        global _gemini_caches
        if '_gemini_caches' not in globals():
            _gemini_caches = {}

        # Prompt Caching logic for Gemini
        cached_content_name = None
        if system_instruction and len(system_instruction) > 1000:
            h = hashlib.sha256((gemini_model + system_instruction).encode()).hexdigest()
            if h in _gemini_caches:
                try:
                    c = await client.aio.caches.get(name=_gemini_caches[h])
                    cached_content_name = c.name
                    logger.info(f"Using existing Gemini cache: {cached_content_name}")
                except Exception:
                    pass
            
            if not cached_content_name:
                try:
                    logger.info("Creating new Gemini cache for system instruction...")
                    cache = await client.aio.caches.create(
                        model=gemini_model,
                        contents=" ",
                        config=types.CreateCachedContentConfig(
                            system_instruction=system_instruction,
                            ttl="3600s"
                        )
                    )
                    cached_content_name = cache.name
                    _gemini_caches[h] = cached_content_name
                    logger.info(f"Created Gemini cache: {cached_content_name}")
                except Exception as e:
                    logger.warning(f"Failed to create Gemini cache (will proceed without cache): {e}")

        sys_instr_to_pass = None if cached_content_name else system_instruction

        config_obj = types.GenerateContentConfig(**gen_config) if gen_config else types.GenerateContentConfig()
        if sys_instr_to_pass:
            config_obj.system_instruction = sys_instr_to_pass
        if cached_content_name:
            config_obj.cached_content = cached_content_name

        if use_search_grounding:
            if response_schema or use_json:
                logger.warning("Gemini API does not support Tool use (Search Grounding) with JSON response schema. Disabling Search Grounding for this call.")
            else:
                config_obj.tools = [types.Tool(google_search=types.GoogleSearch())]

        response = await client.aio.models.generate_content(
            model=gemini_model,
            contents=prompt,
            config=config_obj
        )
        
        text = response.text.strip()

        if response_schema or use_json:
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        usage = {
            "in": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
            "out": response.usage_metadata.candidates_token_count if response.usage_metadata else 0
        }
        return text, usage
