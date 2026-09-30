#!/usr/bin/env python3
"""Generate video scripts from insights, research, and CW stats using OpenRouter."""

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Script
from tools.validate_script import validate_script as validate_script_fn

load_dotenv()

INSIGHTS_FILE = Path(__file__).parent.parent / "out" / "insights.json"
RESEARCH_FILE = Path(__file__).parent.parent / "out" / "research.json"
CW_STATS_FILE = Path(__file__).parent.parent / "out" / "cw_stats.json"
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
OUTPUT_DIR = Path(__file__).parent.parent / "out" / "scripts"
CACHE_DIR = Path(__file__).parent.parent / "out" / ".cache"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

VALID_TYPES = ["pain", "data", "solution"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_inputs_hash() -> str:
    """Generate hash of all input files for cache key."""
    hasher = hashlib.sha256()
    for f in [INSIGHTS_FILE, RESEARCH_FILE, CW_STATS_FILE]:
        if f.exists():
            with open(f, "rb") as fp:
                hasher.update(fp.read())
    return hasher.hexdigest()[:16]


def get_cache_path(script_type: str, inputs_hash: str, model: str) -> Path:
    """Get cache file path for given type and hash."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"script_{script_type}_{inputs_hash}_{model}.json"


def load_from_cache(script_type: str, inputs_hash: str, model: str) -> Script | None:
    """Load script from cache if exists."""
    cache_path = get_cache_path(script_type, inputs_hash, model)
    if cache_path.exists():
        logger.info("Loading %s script from cache: %s", script_type, cache_path)
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Script.model_validate(data)
    return None


def save_to_cache(script_type: str, inputs_hash: str, model: str, script: Script) -> None:
    """Save script to cache."""
    cache_path = get_cache_path(script_type, inputs_hash, model)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(script.model_dump(mode="json"), f, indent=2, default=str)
    logger.info("Saved %s script to cache: %s", script_type, cache_path)


def load_inputs() -> tuple[dict, list, list]:
    """Load insights, research, and CW stats."""
    with open(INSIGHTS_FILE, "r", encoding="utf-8") as f:
        insights = json.load(f)

    with open(RESEARCH_FILE, "r", encoding="utf-8") as f:
        research = json.load(f)

    with open(CW_STATS_FILE, "r", encoding="utf-8") as f:
        cw_stats = json.load(f)

    return insights, research, cw_stats


def load_prompt(script_type: str) -> str:
    """Load prompt template for script type."""
    prompt_file = PROMPTS_DIR / f"{script_type}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    with open(prompt_file, "r", encoding="utf-8") as f:
        return f.read()


def build_user_prompt(script_type: str, insights: dict, research: list, cw_stats: list) -> str:
    """Build user prompt with all inputs."""
    return f"""SCRIPT TYPE: {script_type.upper()}

INSIGHTS (from winning Meta ads):
{json.dumps(insights, indent=2)}

RESEARCH (current market data):
{json.dumps(research[:10], indent=2)}

CW STATS (real performance data - USE THESE EXACT NUMBERS WITH source_ref):
{json.dumps(cw_stats, indent=2)}

Generate a {script_type} script following the prompt template exactly. Return JSON only matching the Script schema."""


def strip_code_fences(text: str) -> str:
    """Strip markdown code fences if present."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
def call_openrouter(api_key: str, model: str, system_prompt: str, user_prompt: str, previous_error: str | None = None) -> str:
    """Call OpenRouter API and return raw response text."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if previous_error:
        messages.append({
            "role": "user",
            "content": f"Previous response failed validation: {previous_error}. Fix and return valid JSON only."
        })

    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": 5000,
    }

    logger.info("Calling OpenRouter for script generation...")
    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return strip_code_fences(content)


def generate_script(script_type: str, api_key: str, model: str, inputs_hash: str, refresh: bool = False) -> Script:
    """Generate a single script of the given type with validation and retry."""
    insights, research, cw_stats = load_inputs()
    system_prompt = load_prompt(script_type)
    user_prompt = build_user_prompt(script_type, insights, research, cw_stats)

    if not refresh:
        cached = load_from_cache(script_type, inputs_hash, model)
        if cached:
            # Validate cached script too
            errors = validate_script_fn(cached.model_dump(mode="json"))
            if not errors:
                logger.info("Using cached %s script", script_type)
                return cached
            else:
                logger.warning("Cached %s script failed validation, regenerating: %s", script_type, errors)

    last_error = None
    for attempt in range(3):
        try:
            response_text = call_openrouter(api_key, model, system_prompt, user_prompt, last_error)
            script_data = json.loads(response_text)
            script_data["type"] = script_type
            script = Script.model_validate(script_data)

            # Additional validation
            errors = validate_script_fn(script.model_dump(mode="json"))
            if errors:
                last_error = "; ".join(errors)
                logger.warning("Attempt %d validation failed for %s: %s", attempt + 1, script_type, last_error)
                if attempt == 2:
                    raise ValueError(f"Validation failed after 3 attempts: {last_error}")
                continue

            logger.info("Generated %s script (%.1fs, %d scenes)", script_type, script.total_runtime, len(script.scenes))
            save_to_cache(script_type, inputs_hash, model, script)
            return script
        except Exception as e:
            last_error = str(e)
            logger.warning("Attempt %d failed for %s: %s", attempt + 1, script_type, e)
            if attempt == 2:
                raise

    raise RuntimeError(f"Failed to generate {script_type} script after 3 attempts")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate video scripts from insights/research/stats")
    parser.add_argument(
        "--type",
        choices=VALID_TYPES + ["all"],
        default="all",
        help="Script type to generate (default: all)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and generate fresh scripts",
    )
    args = parser.parse_args()

    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not found in environment")
        return 1
    if not OPENROUTER_MODEL:
        logger.error("OPENROUTER_MODEL not found in environment")
        return 1

    for f in [INSIGHTS_FILE, RESEARCH_FILE, CW_STATS_FILE]:
        if not f.exists():
            logger.error("Required input not found: %s", f)
            return 1

    for p in ["pain.md", "data.md", "solution.md"]:
        if not (PROMPTS_DIR / p).exists():
            logger.error("Prompt file not found: %s", PROMPTS_DIR / p)
            return 1

    inputs_hash = get_inputs_hash()
    types_to_generate = VALID_TYPES if args.type == "all" else [args.type]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for script_type in types_to_generate:
        try:
            logger.info("=" * 50)
            logger.info("Generating %s script...", script_type)
            script = generate_script(script_type, OPENROUTER_API_KEY, OPENROUTER_MODEL, inputs_hash, args.refresh)

            output_file = OUTPUT_DIR / f"script_{script_type}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(script.model_dump(mode="json"), f, indent=2, default=str)

            logger.info("Saved %s script to %s", script_type, output_file)

        except Exception as e:
            logger.error("Failed to generate %s script: %s", script_type, e)
            return 1

    logger.info("=" * 50)
    logger.info("All scripts generated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())