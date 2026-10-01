#!/usr/bin/env python3
"""Hermes tool wrapper for script_generator."""

import subprocess
import sys
from pathlib import Path


def script_generator(script_type: str = "all", refresh: bool = False) -> dict:
    """Generate video scripts from insights/research/stats."""
    cmd = [sys.executable, "tools/script_generator.py", "--type", script_type]
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
    parser.add_argument("--type", choices=["pain", "data", "solution", "all"], default="all")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    result = script_generator(args.type, args.refresh)
    print(json.dumps(result))