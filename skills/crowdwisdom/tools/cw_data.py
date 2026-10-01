#!/usr/bin/env python3
"""Hermes tool wrapper for cw_data."""

import subprocess
import sys
from pathlib import Path


def cw_data(refresh: bool = False) -> dict:
    """Extract ad-worthy stats from CrowdWisdomTrading data files."""
    cmd = [sys.executable, "tools/cw_data.py"]
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
    result = cw_data(args.refresh)
    print(json.dumps(result))