#!/usr/bin/env python3
"""Extract ad-worthy stats from CrowdWisdomTrading data files."""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import InsightsReport

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = Path(__file__).parent.parent / "out" / "cw_stats.json"
CACHE_DIR = Path(__file__).parent.parent / "out" / ".cache"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_file_hash(filepath: Path) -> str:
    """Generate hash of file content for cache key."""
    with open(filepath, "rb") as f:
        content = f.read()
    return hashlib.sha256(content).hexdigest()[:16]


def get_cache_path(file_hash: str, model: str) -> Path:
    """Get cache file path for given hash."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"cw_stats_{file_hash}_{model}.json"


def load_from_cache(file_hash: str, model: str) -> list[dict] | None:
    """Load stats from cache if exists."""
    cache_path = get_cache_path(file_hash, model)
    if cache_path.exists():
        logger.info("Loading stats from cache: %s", cache_path)
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_to_cache(file_hash: str, model: str, stats: list[dict]) -> None:
    """Save stats to cache."""
    cache_path = get_cache_path(file_hash, model)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)
    logger.info("Saved stats to cache: %s", cache_path)


def load_data_files() -> list[dict]:
    """Load all data files from data/ directory, auto-detecting format."""
    files = list(DATA_DIR.glob("*"))
    data_files = [f for f in files if f.is_file() and f.name != ".gitkeep"]

    if not data_files:
        logger.warning("No data files found in %s", DATA_DIR)
        return []

    all_records = []

    for filepath in data_files:
        logger.info("Loading %s", filepath.name)
        records = load_single_file(filepath)
        if records:
            for r in records:
                r["_source_file"] = filepath.name
                r["_source_path"] = str(filepath)
            all_records.extend(records)

    logger.info("Loaded %d total records from %d files", len(all_records), len(data_files))
    return all_records


def load_single_file(filepath: Path) -> list[dict]:
    """Load a single file based on extension."""
    suffix = filepath.suffix.lower()

    try:
        if suffix == ".json":
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return [data]

        elif suffix == ".csv":
            df = pd.read_csv(filepath)
            return df.to_dict(orient="records")

        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(filepath)
            return df.to_dict(orient="records")

        elif suffix == ".pdf":
            import pypdf
            with open(filepath, "rb") as f:
                reader = pypdf.PdfReader(f)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return [{"text": text, "_file": filepath.name}]

        elif suffix in (".txt", ".md"):
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
            return [{"text": text, "_file": filepath.name}]

        else:
            logger.warning("Unsupported file format: %s", suffix)
            return []

    except Exception as e:
        logger.error("Failed to load %s: %s", filepath, e)
        return []


def extract_candidate_stats(records: list[dict]) -> list[dict]:
    """Extract candidate statistics from records with source references."""
    candidates = []

    for i, record in enumerate(records):
        source_ref = f"{record.get('_source_file', 'unknown')}[{i}]"

        for key, value in record.items():
            if key.startswith("_"):
                continue

            if value is None or value == "":
                continue

            if isinstance(value, (int, float)):
                candidates.append({
                    "stat": key,
                    "value": value,
                    "context": f"From {record.get('_source_file', 'file')} record",
                    "source_ref": source_ref,
                    "source_field": key,
                })
            elif isinstance(value, str):
                if any(kw in key.lower() for kw in ["win", "rate", "return", "profit", "loss", "signal", "trade", "target", "stop", "confidence", "price", "performance", "ratio", "percent", "%", "avg", "average", "best", "worst", "total", "count", "number"]):
                    candidates.append({
                        "stat": key,
                        "value": value,
                        "context": f"From {record.get('_source_file', 'file')} record",
                        "source_ref": source_ref,
                        "source_field": key,
                    })

    logger.info("Extracted %d candidate stats", len(candidates))
    return candidates


SYSTEM_PROMPT = """You are a direct-response ad copywriter for a stock trading signals product.

Given a list of candidate statistics from CrowdWisdomTrading's historical signals data, select the 5-8 most compelling, ad-worthy stats that would grab a trader's attention on-screen.

Rules:
1. ONLY use stats provided in the input - NEVER invent or hallucinate numbers
2. Each selected stat MUST include its source_ref to trace back to the data
3. Phrase each as a short, punchy on-screen text line (max 10 words)
4. Focus on: win rates, returns, best trades, signal counts, time periods, confidence levels, risk/reward
5. Prioritize stats with concrete numbers over qualitative text

Return JSON only with this schema:
{
  "stats": [
    {"stat": "...", "value": "...", "context": "...", "display_text": "...", "source_ref": "..."}
  ]
}"""


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
def call_openrouter(api_key: str, model: str, user_prompt: str) -> str:
    """Call OpenRouter API and return raw response text."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
        "max_tokens": 3000,
    }

    logger.info("Calling OpenRouter with model: %s", model)
    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return content.strip()


def select_ad_worthy_stats(candidates: list[dict], api_key: str, model: str) -> list[dict]:
    """Use LLM to select and phrase the most ad-worthy stats."""
    user_prompt = f"Candidate stats from CrowdWisdomTrading data:\n\n{json.dumps(candidates, indent=2, default=str)}\n\nSelect 5-8 most ad-worthy stats and phrase each as a short on-screen text line."

    response_text = call_openrouter(api_key, model, user_prompt)

    try:
        result = json.loads(response_text)
        stats = result.get("stats", [])
        logger.info("LLM selected %d stats", len(stats))
        return stats
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response: %s", e)
        logger.debug("Raw response: %s", response_text)
        raise


def validate_stats(stats: list[dict], candidates: list[dict]) -> list[dict]:
    """Validate that each stat traces back to a candidate."""
    candidate_map = {c["source_ref"]: c for c in candidates}
    validated = []

    for stat in stats:
        source_ref = stat.get("source_ref")
        if source_ref not in candidate_map:
            logger.warning("Stat references unknown source: %s", source_ref)
            continue

        original = candidate_map[source_ref]
        validated_stat = {
            "stat": stat.get("stat", original["stat"]),
            "value": str(stat.get("value", original["value"])),
            "context": stat.get("context", original["context"]),
            "display_text": stat.get("display_text", ""),
            "source_ref": source_ref,
        }

        if not validated_stat["display_text"]:
            validated_stat["display_text"] = f"{validated_stat['stat']}: {validated_stat['value']}"

        validated.append(validated_stat)

    logger.info("Validated %d stats", len(validated))
    return validated


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract ad-worthy stats from CrowdWisdomTrading data")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh LLM selection",
    )
    args = parser.parse_args()

    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not found in environment")
        return 1
    if not OPENROUTER_MODEL:
        logger.error("OPENROUTER_MODEL not found in environment")
        return 1

    records = load_data_files()
    if not records:
        logger.error("No data records loaded")
        return 1

    combined_hash = hashlib.sha256()
    for r in records:
        combined_hash.update(str(r).encode())
    file_hash = combined_hash.hexdigest()[:16]

    if not args.refresh:
        cached = load_from_cache(file_hash, OPENROUTER_MODEL)
        if cached:
            stats = cached
            logger.info("Using cached stats")
        else:
            logger.info("Cache miss, extracting fresh stats...")
            candidates = extract_candidate_stats(records)
            stats = select_ad_worthy_stats(candidates, OPENROUTER_API_KEY, OPENROUTER_MODEL)
            stats = validate_stats(stats, candidates)
            save_to_cache(file_hash, OPENROUTER_MODEL, stats)
    else:
        logger.info("--refresh flag set, extracting fresh stats...")
        candidates = extract_candidate_stats(records)
        stats = select_ad_worthy_stats(candidates, OPENROUTER_API_KEY, OPENROUTER_MODEL)
        stats = validate_stats(stats, candidates)
        save_to_cache(file_hash, OPENROUTER_MODEL, stats)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)

    logger.info("Written %d stats to %s", len(stats), OUTPUT_FILE)
    for s in stats:
        logger.info("  - %s: %s (%s)", s["display_text"], s["value"], s["source_ref"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
