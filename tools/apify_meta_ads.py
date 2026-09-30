#!/usr/bin/env python3
"""Apify Meta Ad Library scraper for trading-related ads."""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Ad

load_dotenv()

CACHE_DIR = Path(__file__).parent.parent / "out" / ".cache"
OUTPUT_FILE = Path(__file__).parent.parent / "out" / "ads.json"
ACTOR_ID = "apify/facebook-ads-scraper"

KEYWORDS = [
    "trading signals",
    "stock analysis",
    "stock market signals",
    "swing trading",
]

COUNTRIES = ["US"]
ACTIVE_STATUS = "all"
MEDIA_TYPE = "all"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_input_hash(input_data: dict) -> str:
    """Generate SHA256 hash of sorted JSON input."""
    serialized = json.dumps(input_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def get_cache_path(input_hash: str) -> Path:
    """Get cache file path for given input hash."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"apify_{input_hash}.json"


def build_actor_input() -> dict:
    """Build the actor input payload."""
    return {
        "searchKeywords": KEYWORDS,
        "countries": COUNTRIES,
        "activeStatus": ACTIVE_STATUS,
        "mediaType": MEDIA_TYPE,
        "resultsLimit": 1000,
    }


def load_from_cache(input_hash: str) -> list[dict] | None:
    """Load raw dataset items from cache if exists."""
    cache_path = get_cache_path(input_hash)
    if cache_path.exists():
        logger.info("Loading from cache: %s", cache_path)
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_to_cache(input_hash: str, data: list[dict]) -> None:
    """Save raw dataset items to cache."""
    cache_path = get_cache_path(input_hash)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Saved to cache: %s", cache_path)


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
def run_actor(client: ApifyClient, actor_input: dict) -> list[dict]:
    """Run the Apify actor and return dataset items."""
    logger.info("Starting actor run...")
    run = client.actor(ACTOR_ID).call(run_input=actor_input)
    dataset_id = run["defaultDatasetId"]
    logger.info("Actor run completed, fetching dataset: %s", dataset_id)
    items = client.dataset(dataset_id).list_items().items
    logger.info("Retrieved %d items from dataset", len(items))
    return items


def parse_date(date_str: str | None) -> date | None:
    """Parse date string from Apify response."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    logger.warning("Failed to parse date: %s", date_str)
    return None


def normalize_ad(item: dict) -> Ad | None:
    """Normalize raw Apify item to Ad model."""
    try:
        start_date = parse_date(item.get("ad_creation_time") or item.get("start_date"))
        if not start_date:
            logger.warning("Skipping item %s: missing start_date", item.get("id", "unknown"))
            return None

        end_date = parse_date(item.get("ad_delivery_stop_time") or item.get("end_date"))
        is_active = item.get("is_active", True)
        if isinstance(is_active, str):
            is_active = is_active.lower() == "true"

        media_type_raw = item.get("media_type", "other").lower()
        if media_type_raw in ("video", "image"):
            media_type = media_type_raw
        else:
            media_type = "other"

        ad = Ad(
            id=str(item.get("id", "")),
            page_name=item.get("page_name", ""),
            ad_text=item.get("ad_text", "") or item.get("body", "") or "",
            headline=item.get("headline"),
            cta=item.get("cta_text") or item.get("call_to_action"),
            media_type=media_type,
            start_date=start_date,
            end_date=end_date,
            is_active=is_active,
            ad_url=item.get("ad_url") or item.get("ad_snapshot_url"),
            raw=item,
        )
        return ad
    except Exception as e:
        logger.warning("Failed to normalize item %s: %s", item.get("id", "unknown"), e)
        return None


def filter_recent_ads(ads: list[Ad], days: int = 30) -> list[Ad]:
    """Filter ads with start_date within the last N days."""
    cutoff = date.today() - timedelta(days=days)
    filtered = [ad for ad in ads if ad.start_date >= cutoff]
    logger.info("Filtered to %d ads within last %d days", len(filtered), days)
    return filtered


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Meta Ad Library for trading ads")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh data from Apify",
    )
    args = parser.parse_args()

    token = os.getenv("APIFY_TOKEN")
    if not token:
        logger.error("APIFY_TOKEN not found in environment. Set it in .env file.")
        return 1

    actor_input = build_actor_input()
    input_hash = get_input_hash(actor_input)

    if not args.refresh:
        cached = load_from_cache(input_hash)
        if cached is not None:
            raw_items = cached
        else:
            logger.info("Cache miss, fetching from Apify...")
            client = ApifyClient(token)
            raw_items = run_actor(client, actor_input)
            save_to_cache(input_hash, raw_items)
    else:
        logger.info("--refresh flag set, fetching fresh data from Apify...")
        client = ApifyClient(token)
        raw_items = run_actor(client, actor_input)
        save_to_cache(input_hash, raw_items)

    logger.info("Normalizing %d raw items...", len(raw_items))
    ads = []
    for item in raw_items:
        ad = normalize_ad(item)
        if ad:
            ads.append(ad)

    logger.info("Successfully normalized %d ads", len(ads))

    recent_ads = filter_recent_ads(ads, days=30)

    ranked_ads = sorted(recent_ads, key=lambda a: a.days_running, reverse=True)
    top_10 = ranked_ads[:10]

    logger.info("Top 10 ads by days_running:")
    for i, ad in enumerate(top_10, 1):
        logger.info("  %d. %s (page: %s, days: %d)", i, ad.id[:20], ad.page_name, ad.days_running)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump([ad.model_dump(mode="json") for ad in top_10], f, indent=2, default=str)

    logger.info("Written %d ads to %s", len(top_10), OUTPUT_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())