#!/usr/bin/env python3
"""Hermes tool wrapper for run_video_agent."""

import subprocess
import sys
from pathlib import Path


def run_video_agent(script_type: str = "pain", dry_run: bool = False) -> dict:
    """Render script to MP4 with TTS + music, extract review artifacts."""
    cmd = [sys.executable, "tools/run_video_agent.py", "--type", script_type]
    if dry_run:
        cmd.append("--dry-run")
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent.parent, timeout=900)
    
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
    parser.add_argument("--type", choices=["pain", "data", "solution", "all"], default="pain")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_video_agent(args.type, args.dry_run)
    print(json.dumps(result))