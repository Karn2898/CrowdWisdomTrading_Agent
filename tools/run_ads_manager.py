#!/usr/bin/env python3
"""Run the full ads pipeline: apify_meta_ads then insight_extractor."""

import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TOOLS_DIR = Path(__file__).parent


def run_script(script_name: str, args: list[str] | None = None) -> int:
    """Run a Python script and return exit code."""
    script_path = TOOLS_DIR / script_name
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def main() -> int:
    """Run the full pipeline."""
    logger.info("=" * 60)
    logger.info("Starting ads pipeline")
    logger.info("=" * 60)

    # Step 1: Run apify_meta_ads
    logger.info("Step 1/2: Scraping Meta Ad Library...")
    exit_code = run_script("apify_meta_ads.py", sys.argv[1:])
    if exit_code != 0:
        logger.error("apify_meta_ads.py failed with exit code %d", exit_code)
        return exit_code

    # Step 2: Run insight_extractor
    logger.info("Step 2/2: Extracting insights...")
    exit_code = run_script("insight_extractor.py", sys.argv[1:])
    if exit_code != 0:
        logger.error("insight_extractor.py failed with exit code %d", exit_code)
        return exit_code

    logger.info("=" * 60)
    logger.info("Pipeline completed successfully")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())