#!/usr/bin/env python3
"""Extract insights from top ads using OpenRouter LLM."""

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Ad, InsightsReport

load_dotenv()

ADS_FILE = Path(__file__).parent.parent / "out" / "ads.json"
INSIGHTS_FILE = Path(__file__).parent.parent / "out" / "insights.json"
CACHE_DIR = Path(__file__).parent.parent / "out" / ".cache"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a direct-response ad strategist for a stock trading signals/analysis product.

Given the top-running ads, extract for each ad:
1. The core pain/problem the ad addresses
2. The concept/angle/unique mechanism
3. The hook (first line or opening)
4. The ICP (ideal customer profile)

Then summarize across all ads:
- top_pains: list of the most common pains
- top_angles: list of the most common concepts/angles
- top_hooks: list of the most effective hooks

Respond with JSON only matching this schema:
{
  "insights": [
    {"pain": "...", "concept": "...", "hook": "...", "icp": "...", "source_ad_ids": ["..."]}
  ],
  "top_pains": ["..."],
  "top_angles": ["..."],
  "top_hooks": ["..."]
}"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_ads_hash(ads: list[Ad], model: str) -> str:
    """Generate hash of ads data + model for cache key."""
    data = {
        "ads": [ad.model_dump(mode="json", exclude={"raw"}) for ad in ads],
        "model": model,
    }
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def get_cache_path(cache_hash: str) -> Path:
    """Get cache file path for given hash."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"insights_{cache_hash}.json"


def load_from_cache(cache_hash: str) -> InsightsReport | None:
    """Load insights from cache if exists."""
    cache_path = get_cache_path(cache_hash)
    if cache_path.exists():
        logger.info("Loading insights from cache: %s", cache_path)
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return InsightsReport.model_validate(data)
    return None


def save_to_cache(cache_hash: str, report: InsightsReport) -> None:
    """Save insights to cache."""
    cache_path = get_cache_path(cache_hash)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2, default=str)
    logger.info("Saved insights to cache: %s", cache_path)


def load_ads() -> list[Ad]:
    """Load and validate ads from JSON file."""
    if not ADS_FILE.exists():
        logger.error("Ads file not found: %s", ADS_FILE)
        sys.exit(1)

    with open(ADS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    ads = []
    for item in data:
        try:
            ads.append(Ad.model_validate(item))
        except Exception as e:
            logger.warning("Skipping invalid ad: %s", e)

    logger.info("Loaded %d valid ads from %s", len(ads), ADS_FILE)
    return ads


def build_user_prompt(ads: list[Ad]) -> str:
    """Build user prompt with ad data."""
    ad_summaries = []
    for ad in ads:
        summary = {
            "id": ad.id,
            "page_name": ad.page_name,
            "ad_text": ad.ad_text[:500] if ad.ad_text else "",
            "headline": ad.headline,
            "cta": ad.cta,
            "media_type": ad.media_type,
            "days_running": ad.days_running,
        }
        ad_summaries.append(summary)

    return f"Analyze these top-performing ads:\n\n{json.dumps(ad_summaries, indent=2)}"


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
def call_openrouter(api_key: str, model: str, user_prompt: str, previous_error: str | None = None) -> str:
    """Call OpenRouter API and return raw response text."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    if previous_error:
        messages.append({
            "role": "user",
            "content": f"Previous response was invalid. Error: {previous_error}. Please fix and respond with valid JSON only."
        })

    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
        "max_tokens": 4000,
    }

    logger.info("Calling OpenRouter with model: %s", model)
    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return strip_code_fences(content)


def extract_insights(ads: list[Ad], api_key: str, model: str) -> InsightsReport:
    """Extract insights from ads using LLM with retry logic."""
    user_prompt = build_user_prompt(ads)
    last_error = None

    for attempt in range(3):
        try:
            response_text = call_openrouter(api_key, model, user_prompt, last_error)
            report = InsightsReport.model_validate_json(response_text)
            logger.info("Successfully extracted insights on attempt %d", attempt + 1)
            return report
        except Exception as e:
            last_error = str(e)
            logger.warning("Attempt %d failed: %s", attempt + 1, e)
            if attempt == 2:
                raise

    raise RuntimeError("Failed to extract insights after 3 attempts")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract insights from top ads using OpenRouter")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh insights from LLM",
    )
    args = parser.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL")

    if not api_key:
        logger.error("OPENROUTER_API_KEY not found in environment")
        return 1
    if not model:
        logger.error("OPENROUTER_MODEL not found in environment")
        return 1

    ads = load_ads()
    if not ads:
        logger.error("No valid ads to analyze")
        return 1

    cache_hash = get_ads_hash(ads, model)

    if not args.refresh:
        cached = load_from_cache(cache_hash)
        if cached:
            report = cached
            logger.info("Using cached insights")
        else:
            logger.info("Cache miss, extracting fresh insights...")
            report = extract_insights(ads, api_key, model)
            save_to_cache(cache_hash, report)
    else:
        logger.info("--refresh flag set, extracting fresh insights...")
        report = extract_insights(ads, api_key, model)
        save_to_cache(cache_hash, report)

    INSIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INSIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2, default=str)

    logger.info("Written insights to %s", INSIGHTS_FILE)
    logger.info("Total insights: %d", len(report.insights))
    logger.info("Top pains: %s", report.top_pains)
    logger.info("Top angles: %s", report.top_angles)
    logger.info("Top hooks: %s", report.top_hooks)

    return 0


if __name__ == "__main__":
    sys.exit(main())
