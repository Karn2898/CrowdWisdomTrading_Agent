#!/usr/bin/env python3
"""Hermes tool wrapper for tavily_search."""

import subprocess
import sys
from pathlib import Path


def tavily_search(refresh: bool = False) -> dict:
    """Research pain points from insights using Tavily/Exa."""
    cmd = [sys.executable, "tools/tavily_search.py"]
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
    result = tavily_search(args.refresh)
    print(json.dumps(result))