#!/usr/bin/env python3
"""Hermes tool wrapper for validate_script."""

import subprocess
import sys
from pathlib import Path


def validate_script(script_path: str) -> dict:
    """Validate a script JSON against schema and constraints."""
    cmd = [sys.executable, "tools/validate_script.py", script_path]
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent.parent, timeout=120)
    
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
    parser.add_argument("script_path")
    args = parser.parse_args()
    result = validate_script(args.script_path)
    print(json.dumps(result))