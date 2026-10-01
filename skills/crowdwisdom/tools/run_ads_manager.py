#!/usr/bin/env python3
"""Hermes tool wrapper for run_ads_manager."""

import subprocess
import sys
from pathlib import Path


def run_ads_manager(refresh: bool = False) -> dict:
    """Run the ads pipeline: scrape Meta ads + extract insights."""
    cmd = [sys.executable, "tools/run_ads_manager.py"]
    if refresh:
        cmd.append("--refresh")
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent.parent, timeout=600)
    
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode
    }


if __name__ == "__main__":
    import json
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    result = run_ads_manager(args.refresh)
    print(json.dumps(result))