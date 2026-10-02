#!/usr/bin/env python3
"""Render script JSON to MP4 using OpenMontage composition engine with Piper TTS."""

import os
import shutil


import argparse
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

def resolve_ffmpeg_executable() -> str:
    """Resolve ffmpeg binary from env override or PATH."""
    env_path = os.getenv("FFMPEG_PATH")
    if env_path:
        ffmpeg_path = Path(env_path)
        if ffmpeg_path.exists():
            return str(ffmpeg_path)
        raise FileNotFoundError(f"FFMPEG_PATH is set but does not exist: {ffmpeg_path}")

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path
    raise FileNotFoundError(
        "ffmpeg not found. Install ffmpeg and add it to PATH, or set FFMPEG_PATH to ffmpeg.exe."
    )


FFMPEG_BIN = resolve_ffmpeg_executable()

sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "OpenMontage"))

from tools.base_tool import BaseTool, ToolResult
from tools.video.video_compose import VideoCompose

# Add our schemas
sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Script

# Import our TTS module - ensure we import from our tools, not OpenMontage
import importlib.util
tts_path = Path(__file__).parent / "tts_piper.py"
spec = importlib.util.spec_from_file_location("tts_piper", tts_path)
tts_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tts_module)
generate_voiceover = tts_module.generate_voiceover
adjust_scene_durations = tts_module.adjust_scene_durations
adjust_scene_durations = tts_module.adjust_scene_durations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_MUSIC_DIR = Path(__file__).parent.parent / "assets" / "music"


def find_music_track() -> Path | None:
    """Find a royalty-free music track in assets/music/."""
    if not DEFAULT_MUSIC_DIR.exists():
        return None
    
    for ext in (".mp3", ".wav", ".ogg", ".flac"):
        tracks = list(DEFAULT_MUSIC_DIR.glob(f"*{ext}"))
        if tracks:
            logger.info("Found music track: %s", tracks[0])
            return tracks[0]
    
    logger.warning("No music tracks found in %s", DEFAULT_MUSIC_DIR)
    return None


def script_to_edit_decisions(script: Script, project_dir: Path, audio_path: Path = None) -> dict[str, Any]:
    """Convert our Script schema to OpenMontage edit_decisions format."""
    
    cuts = []
    
    # Hook scene
    hook_duration = getattr(script.hook, 'duration_sec', 3.0) or 3.0
    cuts.append({
        "id": "hook",
        "source": "",
        "in_seconds": 0.0,
        "out_seconds": hook_duration,
        "type": "text_card",
        "text": script.hook.first_3s,
        "transition_in": "fade",
        "transition_out": "cut",
        "transition_duration": 0.5,
        "layer": "primary",
        "reason": "Scroll-stopping hook"
    })
    
    # Script scenes
    for i, scene in enumerate(script.scenes):
        duration = scene.duration_sec
        cuts.append({
            "id": f"scene_{i+1}",
            "source": "",
            "in_seconds": 0.0,
            "out_seconds": duration,
            "type": "generated",
            "text": scene.on_screen_text or scene.visual,
            "transition_in": "cut",
            "transition_out": "fade" if i == len(script.scenes) - 1 else "cut",
            "transition_duration": 0.5,
            "layer": "primary",
            "reason": f"Scene {i+1}: {scene.visual[:50]}"
        })
    
    # CTA scene
    cta_duration = getattr(script, 'cta_duration', 3.0) or 3.0
    cuts.append({
        "id": "cta",
        "source": "",
        "in_seconds": 0.0,
        "out_seconds": cta_duration,
        "type": "text_card",
        "text": script.cta,
        "transition_in": "fade",
        "transition_out": "fade",
        "transition_duration": 1.0,
        "layer": "primary",
        "reason": "Call to action"
    })
    
    # Determine renderer_family based on script type
    renderer_family_map = {
        "pain": "explainer-teacher",
        "data": "explainer-data",
        "solution": "product-reveal"
    }
    
    # Build narration segments for video_compose
    narration_segments = []
    current_time = 0.0
    
    # Hook narration
    if script.hook.first_3s:
        narration_segments.append({
            "asset_id": "hook_vo",
            "start_seconds": current_time
        })
    current_time += hook_duration
    
    # Scene narrations
    for i, scene in enumerate(script.scenes):
        if scene.vo:
            narration_segments.append({
                "asset_id": f"scene_{i}_vo",
                "start_seconds": current_time
            })
        current_time += scene.duration_sec
    
    # CTA narration
    if script.cta:
        narration_segments.append({
            "asset_id": "cta_vo",
            "start_seconds": current_time
        })
    
    return {
        "version": "1.0",
        "cuts": cuts,
        "render_runtime": "remotion",
        "renderer_family": renderer_family_map.get(script.type, "explainer-data"),
        "composition_mode": "templated",
        "audio": {
            "narration": {
                "segments": narration_segments
            },
            "music": {
                "asset_id": "background_music",
                "volume": 0.15,
                "fade_in_seconds": 2.0,
                "fade_out_seconds": 2.0,
                "ducking": {
                    "enabled": True,
                    "threshold_db": -20,
                    "reduction_db": 12,
                    "attack_ms": 100,
                    "release_ms": 500
                }
            },
            "sfx": []
        },
        "subtitles": {
            "enabled": True,
            "style": "word-by-word",
            "source": "",
            "font": "Inter",
            "font_size": 28,
            "color": "#FFFFFF",
            "outline_color": "#000000",
            "background": "#00000088",
            "position": "bottom-center",
            "max_words_per_line": 6
        },
        "metadata": {
            "script_type": script.type,
            "total_runtime": script.total_runtime,
            "compose_target": {
                "width": 1080,
                "height": 1920,
                "fit": "cover"
            },
            "audio_path": str(audio_path) if audio_path else ""
        }
    }


def create_asset_manifest(project_dir: Path, audio_path: Path = None) -> dict[str, Any]:
    """Create asset_manifest for video_compose."""
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    
    assets = []
    
    # Add audio assets if available
    if audio_path and audio_path.exists():
        assets.append({
            "id": "background_music",
            "type": "audio",
            "path": str(audio_path),
            "description": "Background music mixed with VO"
        })
    
    return {
        "version": "1.0",
        "assets": assets,
        "metadata": {
            "project_dir": str(project_dir)
        }
    }


def render_script(
    script_path: str,
    out_dir: str = "out/videos",
    dry_run: bool = False,
    timeout: int = 300,
    music_path: str = None
) -> Path:
    """Render script JSON to MP4 using OpenMontage video_compose with TTS."""
    
    script_file = Path(script_path)
    if not script_file.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    
    with open(script_file, "r") as f:
        script_data = json.load(f)
    
    script = Script.model_validate(script_data)
    
    # Output path
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    output_file = out_path / f"video_{script.type}.mp4"
    
    # Create project directory structure
    project_dir = out_path / f"project_{script.type}"
    project_dir.mkdir(parents=True, exist_ok=True)
    
    # Resolve music path
    if music_path:
        music_file = Path(music_path)
    else:
        music_file = find_music_track()
    
    if dry_run:
        # Write brief/plan files for inspection
        brief_file = project_dir / "brief.json"
        with open(brief_file, "w") as f:
            json.dump({
                "title": f"CrowdWisdomTrading {script.type} video",
                "topic": script.type,
                "duration_sec": script.total_runtime,
                "hook": script.hook.first_3s,
                "scenes": len(script.scenes),
                "cta": script.cta
            }, f, indent=2)
        
        edit_decisions = script_to_edit_decisions(script, project_dir)
        edit_file = project_dir / "edit_decisions.json"
        with open(edit_file, "w") as f:
            json.dump(edit_decisions, f, indent=2)
        
        manifest_file = project_dir / "asset_manifest.json"
        with open(manifest_file, "w") as f:
            json.dump(create_asset_manifest(project_dir), f, indent=2)
        
        logger.info("Dry run complete. Files written to %s", project_dir)
        return output_file
    
    # Generate voiceover with Piper TTS
    logger.info("Generating voiceover with Piper TTS...")
    total_vo_duration, segments = generate_voiceover(script_data, project_dir, music_file)
    
    # Adjust scene durations to match actual VO
    logger.info("Adjusting scene durations to match VO (%.1fs)...", total_vo_duration)
    adjusted_script = adjust_scene_durations(script_data, segments)
    
    # Write adjusted script for reference
    adjusted_file = project_dir / "script_adjusted.json"
    with open(adjusted_file, "w") as f:
        json.dump(adjusted_script, f, indent=2)
    logger.info("Adjusted script written to %s", adjusted_file)
    
    # Re-validate with adjusted durations
    script = Script.model_validate(adjusted_script)
    
    # Final audio path (mixed VO + music)
    final_audio_path = project_dir / "audio" / "final_mix.wav"
    
    # Convert to OpenMontage format with audio path
    edit_decisions = script_to_edit_decisions(script, project_dir, final_audio_path)
    asset_manifest = create_asset_manifest(project_dir, final_audio_path)
    
    # Initialize video_compose tool
    composer = VideoCompose()
    
    # Check available runtimes
    info = composer.get_info()
    render_engines = info.get("render_engines", {})
    logger.info("Available render engines: %s", render_engines)
    
# Prepare inputs for video_compose
    # Try render operation first (HyperFrames/Remotion), fallback to direct ffmpeg
    if render_engines.get("hyperframes", False):
        operation = "render"
        edit_decisions["render_runtime"] = "hyperframes"
        logger.info("Using HyperFrames render")
    elif render_engines.get("remotion", False):
        operation = "render"
        edit_decisions["render_runtime"] = "remotion"
        logger.info("Using Remotion render")
    else:
        # Use direct ffmpeg instead of buggy compose operation
        logger.info("Using direct FFmpeg for video creation")
        return render_with_ffmpeg_direct(script, project_dir, final_audio_path, output_file, edit_decisions)
    
    inputs = {
        "operation": operation,
        "edit_decisions": edit_decisions,
        "asset_manifest": asset_manifest,
        "output_path": str(output_file),
        "profile": "tiktok",
        "subtitle_burn": True,
        "two_pass_encode": True,
        "audio_path": str(final_audio_path) if final_audio_path.exists() else None
    }
    
    logger.info("Starting render to %s (operation=%s)", output_file, operation)
    start = time.time()
    
    try:
        result = composer.execute(inputs)
    except Exception as e:
        logger.error("Render failed: %s", e)
        raise
    
    elapsed = time.time() - start
    
    if not result.success:
        logger.error("Render failed: %s", result.error)
        logger.warning("Video render failed but audio is available at %s", final_audio_path)
        return str(final_audio_path)
    
    if not output_file.exists():
        logger.warning("Render completed but output file missing: %s", output_file)
        return str(final_audio_path)
    
    logger.info("Render successful in %.1fs: %s", elapsed, output_file)
    return str(output_file)


def render_with_ffmpeg_direct(
    script: Script,
    project_dir: Path,
    audio_path: Path,
    output_file: Path,
    edit_decisions: dict
) -> str:
    """Create video directly with FFmpeg - solid color background matching script duration."""
    logger.info("Creating video directly with FFmpeg (solid color)...")
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    cuts = edit_decisions.get("cuts", [])
    if not cuts:
        logger.error("No cuts in edit_decisions")
        return str(audio_path)
    
    # Use script's total_runtime as target duration
    target_duration = script.total_runtime
    
    # Simple solid color video matching target duration, pad audio to match
    bg_color = "0x1a1a2e"  # Dark blue background
    
    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "lavfi",
        "-i", f"color=c={bg_color}:size=1080x1920:duration={target_duration}:rate=30",
        "-i", str(audio_path),
        "-filter_complex", "[1:a]apad=whole_dur={}[aout]".format(target_duration),
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(target_duration),
        str(output_file)
    ]
    
    try:
        import subprocess
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error("FFmpeg direct render failed: %s", result.stderr)
            return str(audio_path)
        
        if output_file.exists():
            logger.info("Direct FFmpeg render successful: %s", output_file)
            return str(output_file)
        else:
            logger.error("FFmpeg render completed but output missing")
            return str(audio_path)
    except Exception as e:
        logger.error("Direct FFmpeg render exception: %s", e)
        return str(audio_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render script JSON to MP4 via OpenMontage with Piper TTS")
    parser.add_argument("script", help="Path to script JSON file")
    parser.add_argument(
        "--out-dir",
        default="out/videos",
        help="Output directory for MP4 (default: out/videos)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only write brief/plan files, don't render"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Render timeout in seconds (default: 300)"
    )
    parser.add_argument(
        "--music",
        help="Path to music file (default: auto-detect from assets/music/)"
    )
    args = parser.parse_args()
    
    try:
        output = render_script(args.script, args.out_dir, args.dry_run, args.timeout, args.music)
        logger.info("Output: %s", output)
        return 0
    except Exception as e:
        logger.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
