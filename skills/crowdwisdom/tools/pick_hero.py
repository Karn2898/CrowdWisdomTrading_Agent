#!/usr/bin/env python3
"""Hermes tool wrapper for pick_hero."""
import subprocess
import sys
from pathlib import Path


def pick_hero(dry_run: bool = False) -> dict:
    """Score scripts/videos with LLM and copy the winner to out/hero.mp4."""
    cmd = [sys.executable, "tools/pick_hero.py"]
    if dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent.parent.parent,
        timeout=300,
    )

    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = pick_hero(args.dry_run)
    print(json.dumps(result))
