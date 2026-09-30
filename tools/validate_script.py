#!/usr/bin/env python3
"""Validate script JSON against Schema and additional constraints."""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Script

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

WORDS_PER_SECOND = 3.0


def validate_script(script_data: dict) -> list[str]:
    """Validate script and return list of human-readable errors."""
    errors = []

    # 1. Validate with Pydantic model
    try:
        script = Script.model_validate(script_data)
    except Exception as e:
        errors.append(f"Schema validation failed: {e}")
        return errors

    # 2. Hook presence and first_3s
    if not script.hook:
        errors.append("Missing hook object")
    else:
        if not script.hook.first_3s or not script.hook.first_3s.strip():
            errors.append("hook.first_3s is missing or empty (must be a scroll-stopper)")

    # 3. Total duration 30-60s
    if script.total_runtime < 30 or script.total_runtime > 60:
        errors.append(f"Total runtime {script.total_runtime:.1f}s outside 30-60s range")

    # 4. Scene count 5-7
    scene_count = len(script.scenes)
    if scene_count < 5 or scene_count > 7:
        errors.append(f"Scene count {scene_count} outside 5-7 range")

    # 5. Per-scene validation
    for i, scene in enumerate(script.scenes, 1):
        # Empty VO
        if not scene.vo or not scene.vo.strip():
            errors.append(f"Scene {i}: VO is empty")

        # VO word count vs duration at 3 words/sec
        vo_words = len(scene.vo.split())
        max_words = scene.duration_sec * WORDS_PER_SECOND
        if vo_words > max_words:
            errors.append(
                f"Scene {i}: VO has {vo_words} words but only {scene.duration_sec:.1f}s "
                f"(max {max_words:.0f} words at {WORDS_PER_SECOND} words/sec)"
            )

        # Duration positive
        if scene.duration_sec <= 0:
            errors.append(f"Scene {i}: duration_sec must be positive")

    return errors


def validate_file(filepath: Path) -> list[str]:
    """Validate a script JSON file."""
    if not filepath.exists():
        return [f"File not found: {filepath}"]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]

    return validate_script(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate script JSON against schema and constraints")
    parser.add_argument("file", help="Path to script JSON file")
    args = parser.parse_args()

    filepath = Path(args.file)
    errors = validate_file(filepath)

    if errors:
        logger.error("Validation FAILED for %s:", filepath)
        for err in errors:
            logger.error("  - %s", err)
        return 1
    else:
        logger.info("Validation PASSED for %s", filepath)
        return 0


if __name__ == "__main__":
    sys.exit(main())