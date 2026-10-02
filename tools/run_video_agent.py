#!/usr/bin/env python3
"""Run video agent: render scripts end-to-end with validation and review artifacts."""

import os
import shutil
from pathlib import Path

import argparse
import json
import logging
import subprocess
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Script

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SCRIPT_TYPES = ["pain", "data", "solution"]
SCRIPTS_DIR = Path(__file__).parent.parent / "out" / "scripts"
VIDEOS_DIR = Path(__file__).parent.parent / "out" / "videos"


def resolve_binary(env_var: str, default_name: str) -> str:
    """Resolve executable path from env var override or PATH."""
    env_path = os.getenv(env_var)
    if env_path:
        binary_path = Path(env_path)
        if binary_path.exists():
            return str(binary_path)
        raise FileNotFoundError(f"{env_var} is set but does not exist: {binary_path}")

    resolved = shutil.which(default_name)
    if resolved:
        return resolved
    raise FileNotFoundError(
        f"{default_name} not found. Install ffmpeg tools and add to PATH, or set {env_var}."
    )


FFMPEG_PATH = resolve_binary("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = resolve_binary("FFPROBE_PATH", "ffprobe")


def run_command(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run command and return result."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    if not video_path.exists():
        return 0.0
    
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    result = run_command(cmd)
    if result.returncode != 0:
        logger.warning("ffprobe failed for %s: %s", video_path, result.stderr)
        return 0.0
    
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def extract_first_3s(video_path: Path, output_path: Path) -> bool:
    """Extract first 3 seconds of video."""
    if not video_path.exists():
        return False
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(video_path),
        "-t", "3",
        "-c", "copy",
        str(output_path)
    ]
    result = run_command(cmd)
    return result.returncode == 0 and output_path.exists()


def extract_frames(video_path: Path, output_dir: Path, count: int = 3) -> list[Path]:
    """Extract frames from video at evenly spaced intervals."""
    if not video_path.exists():
        return []
    
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    
    # Get duration to space frames
    duration = get_video_duration(video_path)
    if duration <= 0:
        return []
    
    for i in range(count):
        timestamp = duration * (i + 1) / (count + 1)
        frame_path = output_dir / f"frame_{i+1:02d}.jpg"
        cmd = [
            FFMPEG_PATH, "-y",
            "-ss", str(timestamp),
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(frame_path)
        ]
        result = run_command(cmd)
        if result.returncode == 0 and frame_path.exists():
            frames.append(frame_path)
    
    return frames


def render_script_type(script_type: str, dry_run: bool = False) -> dict[str, Any]:
    """Render a single script type and return results."""
    script_path = SCRIPTS_DIR / f"script_{script_type}.json"
    if not script_path.exists():
        return {
            "type": script_type,
            "status": "fail",
            "error": f"Script not found: {script_path}",
            "duration": 0.0,
            "video_path": None,
            "first3s_path": None,
            "frames": []
        }
    
    # Import and run montage_render
    sys.path.insert(0, str(Path(__file__).parent))
    import importlib.util
    montage_path = Path(__file__).parent / "montage_render.py"
    spec = importlib.util.spec_from_file_location("montage_render", montage_path)
    montage_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(montage_module)
    
    start = time.time()
    try:
        if dry_run:
            montage_module.render_script(str(script_path), dry_run=True)
            elapsed = time.time() - start
            return {
                "type": script_type,
                "status": "dry-run",
                "error": None,
                "duration": 0.0,
                "video_path": None,
                "first3s_path": None,
                "frames": [],
                "elapsed": elapsed
            }
        
        output = montage_module.render_script(str(script_path))
        elapsed = time.time() - start
        
        # Find the actual video file
        video_path = VIDEOS_DIR / f"video_{script_type}.mp4"
        is_video = video_path.exists()
        if not is_video and output:
            output_path = Path(output)
            if output_path.suffix == ".mp4" and output_path.exists():
                video_path = output_path
                is_video = True
            elif output_path.suffix == ".wav" and output_path.exists():
                # Render failed, only audio available
                is_video = False
        
        duration = get_video_duration(video_path) if video_path.exists() else 0.0
        
        # Validate duration (only for video)
        duration_ok = 30.0 <= duration <= 60.0 if is_video else False
        
        # Extract first 3s and frames (only for video)
        first3s_path = None
        frames = []
        if is_video and video_path.exists():
            first3s_path = VIDEOS_DIR / f"{script_type}_first3s.mp4"
            extract_first_3s(video_path, first3s_path)
            
            frames_dir = VIDEOS_DIR / f"{script_type}_frames"
            frames = extract_frames(video_path, frames_dir, 3)
        
        error_msg = None if duration_ok else (f"Duration {duration:.1f}s outside 30-60s" if is_video else "Video render failed (audio only)")
        
        return {
            "type": script_type,
            "status": "pass" if duration_ok else "fail",
            "error": error_msg,
            "duration": duration,
            "video_path": str(video_path) if is_video and video_path.exists() else None,
            "first3s_path": str(first3s_path) if first3s_path and first3s_path.exists() else None,
            "frames": [str(f) for f in frames],
            "elapsed": elapsed
        }
        
    except Exception as e:
        logger.error("Render failed for %s: %s", script_type, e)
        elapsed = time.time() - start
        return {
            "type": script_type,
            "status": "fail",
            "error": str(e),
            "duration": 0.0,
            "video_path": None,
            "first3s_path": None,
            "frames": [],
            "elapsed": elapsed
        }


def print_results_table(results: list[dict[str, Any]]) -> None:
    """Print pass/fail table."""
    print("\n" + "=" * 100)
    print(f"{'TYPE':<10} {'STATUS':<8} {'DURATION':<10} {'VIDEO':<40} {'FIRST3S':<30} {'FRAMES':<10} {'ELAPSED':<8}")
    print("-" * 100)
    
    for r in results:
        status = r["status"]
        status_display = "[PASS]" if status == "pass" else "[FAIL]" if status == "fail" else status.upper()
        duration = f"{r['duration']:.1f}s" if r['duration'] > 0 else "N/A"
        video = str(r['video_path'])[:38] + ".." if r['video_path'] and len(str(r['video_path'])) > 40 else str(r['video_path'] or "N/A")
        first3s = "OK" if r['first3s_path'] else "NO"
        frames_count = len(r['frames'])
        elapsed = f"{r['elapsed']:.1f}s"
        
        print(f"{r['type']:<10} {status_display:<8} {duration:<10} {video:<40} {first3s:<30} {frames_count:<10} {elapsed:<8}")
        
        if r['error']:
            print(f"  Error: {r['error']}")
        if r['frames']:
            print(f"  Frames: {', '.join(Path(f).name for f in r['frames'])}")
    
    print("=" * 100)
    
    # Summary
    passed = sum(1 for r in results if r['status'] == 'pass')
    failed = sum(1 for r in results if r['status'] == 'fail')
    dry_run = sum(1 for r in results if r['status'] == 'dry-run')
    print(f"Summary: {passed} passed, {failed} failed, {dry_run} dry-run")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run video agent: render scripts end-to-end")
    parser.add_argument(
        "--type",
        choices=SCRIPT_TYPES + ["all"],
        default="pain",
        help="Script type to render (default: pain)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only validate scripts, don't render"
    )
    args = parser.parse_args()
    
    types_to_render = SCRIPT_TYPES if args.type == "all" else [args.type]
    
    print(f"\nVideo Agent - Rendering: {', '.join(types_to_render)}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'FULL RENDER'}")
    
    results = []
    for script_type in types_to_render:
        print(f"\nRendering {script_type}...")
        result = render_script_type(script_type, args.dry_run)
        results.append(result)
    
    print_results_table(results)
    
    # Exit with failure if any failed
    failed = sum(1 for r in results if r['status'] == 'fail')
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())