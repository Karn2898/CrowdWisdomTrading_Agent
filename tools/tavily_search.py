#!/usr/bin/env python3
"""Search for pain point research using Tavily with Exa fallback."""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import InsightsReport

load_dotenv()

INSIGHTS_FILE = Path(__file__).parent.parent / "out" / "insights.json"
RESEARCH_FILE = Path(__file__).parent.parent / "out" / "research.json"
CACHE_DIR = Path(__file__).parent.parent / "out" / ".cache"

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
EXA_API_KEY = os.getenv("EXA_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_query_hash(query: str, source: str) -> str:
    """Generate hash for cache key."""
    data = f"{source}:{query}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def get_cache_path(query_hash: str) -> Path:
    """Get cache file path for given hash."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"search_{query_hash}.json"


def load_from_cache(query_hash: str) -> list[dict] | None:
    """Load search results from cache if exists."""
    cache_path = get_cache_path(query_hash)
    if cache_path.exists():
        logger.debug("Cache hit: %s", cache_path)
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_to_cache(query_hash: str, results: list[dict]) -> None:
    """Save search results to cache."""
    cache_path = get_cache_path(query_hash)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    logger.debug("Cached results: %s", cache_path)


def load_insights() -> InsightsReport:
    """Load and validate insights report."""
    if not INSIGHTS_FILE.exists():
        logger.error("Insights file not found: %s", INSIGHTS_FILE)
        sys.exit(1)

    with open(INSIGHTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return InsightsReport.model_validate(data)


def build_queries(report: InsightsReport) -> list[str]:
    """Build search queries from top pains and ICPs."""
    queries = []

    for pain in report.top_pains[:5]:
        queries.append(f"{pain} stock trading signals")

    for angle in report.top_angles[:3]:
        queries.append(f"{angle} trading strategy")

    for hook in report.top_hooks[:3]:
        queries.append(f"{hook} trading education")

    icps = [i.icp for i in report.insights if i.icp]
    for icp in list(set(icps))[:3]:
        queries.append(f"{icp} trading challenges")

    unique_queries = list(dict.fromkeys(queries))
    logger.info("Built %d unique queries", len(unique_queries))
    return unique_queries


def normalize_result(item: dict, source: str) -> dict:
    """Normalize search result to standard format."""
    return {
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "snippet": item.get("content") or item.get("snippet") or item.get("text", ""),
        "published_date": item.get("published_date") or item.get("publishedDate"),
        "source": source,
    }


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
def search_tavily(query: str) -> list[dict]:
    """Search using Tavily API."""
    from tavily import TavilyClient

    client = TavilyClient(api_key=TAVILY_API_KEY)
    logger.info("Tavily search: %s", query)

    response = client.search(
        query=query,
        search_depth="advanced",
        time_range="month",
        max_results=5,
        include_answer=True,
    )

    results = response.get("results", [])
    return [normalize_result(r, "tavily") for r in results]


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
def search_exa(query: str) -> list[dict]:
    """Search using Exa API as fallback."""
    from exa_py import Exa

    client = Exa(api_key=EXA_API_KEY)
    start_date = (date.today() - timedelta(days=30)).isoformat()
    logger.info("Exa search: %s", query)

    response = client.search_and_contents(
        query=query,
        start_published_date=start_date,
        num_results=5,
        text=True,
        highlights=True,
    )

    results = response.get("results", [])
    return [normalize_result(r, "exa") for r in results]


def dedupe_results(results: list[dict]) -> list[dict]:
    """Deduplicate results by URL."""
    seen = set()
    unique = []
    for r in results:
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(r)
    return unique


def search_pains(insights_path: str | None = None) -> list[dict]:
    """Main search function using Tavily with Exa fallback."""
    if insights_path:
        global INSIGHTS_FILE
        INSIGHTS_FILE = Path(insights_path)

    report = load_insights()
    queries = build_queries(report)

    all_results = []

    for query in queries:
        query_hash = get_query_hash(query, "tavily")

        if not args.refresh if "args" in globals() else True:
            cached = load_from_cache(query_hash)
            if cached:
                logger.info("Cache hit for query: %s", query[:50])
                all_results.extend(cached)
                continue

        try:
            if TAVILY_API_KEY:
                results = search_tavily(query)
            else:
                raise RuntimeError("TAVILY_API_KEY not set")
        except Exception as e:
            logger.warning("Tavily failed for '%s': %s. Trying Exa...", query, e)
            if not EXA_API_KEY:
                logger.error("EXA_API_KEY not set, cannot fallback")
                continue
            try:
                results = search_exa(query)
            except Exception as e2:
                logger.error("Exa also failed for '%s': %s", query, e2)
                continue

        if results:
            save_to_cache(query_hash, results)
            all_results.extend(results)

    deduped = dedupe_results(all_results)
    logger.info("Total unique results: %d", len(deduped))
    return deduped


def main() -> int:
    global args
    parser = argparse.ArgumentParser(description="Search for pain point research using Tavily/Exa")
    parser.add_argument(
        "--insights",
        default=str(INSIGHTS_FILE),
        help="Path to insights.json",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh results",
    )
    args = parser.parse_args()

    if not TAVILY_API_KEY and not EXA_API_KEY:
        logger.error("Neither TAVILY_API_KEY nor EXA_API_KEY found in environment")
        return 1

    try:
        results = search_pains(args.insights)

        RESEARCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RESEARCH_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

        logger.info("Written %d results to %s", len(results), RESEARCH_FILE)
        return 0

    except Exception as e:
        logger.error("Search failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())