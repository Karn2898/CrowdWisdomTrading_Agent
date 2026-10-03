#!/usr/bin/env python3
"""Render a script into a narrated vertical ad."""
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import wave
from pathlib import Path
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "OpenMontage"))

from tools.video.video_compose import VideoCompose

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Script

import importlib.util
tts_path = Path(__file__).parent / "tts_piper.py"
spec = importlib.util.spec_from_file_location("tts_piper", tts_path)
tts_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tts_module)
generate_voiceover = tts_module.generate_voiceover
adjust_scene_durations = tts_module.adjust_scene_durations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_MUSIC_DIR = Path(__file__).parent.parent / "assets" / "music"
DEFAULT_STATS_FILE = Path(__file__).parent.parent / "out" / "cw_stats.json"

W, H, FPS = 1080, 1920, 30


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


# Color grade filter chains applied via ffmpeg. Keys are the --style choices.
STYLES: dict[str, dict[str, Any]] = {
    "teal_orange": {
        "label": "High-contrast teal & orange",
        "video_filters": [
            "eq=contrast=1.35:saturation=1.25:brightness=-0.02:gamma=1.05",
            "colorbalance=rs=-0.15:gs=-0.05:bs=0.20:rm=0.08:gm=0.02:bm=-0.10:rh=0.10:gh=0.05:bh=-0.15",
            "curves=r='0/0 0.5/0.55 1/1':b='0/0.06 0.5/0.45 1/0.96'",
            "vignette=PI/5",
            "unsharp=5:5:0.6",
        ],
        "bg_color": "0x0e1a24",       # dark teal base
        "accent_color": "#ff8c1a",    # orange accent for big numbers
        "text_color": "#f2f6f8",
    },
    "dark_finance": {
        "label": "Dark finance (charcoal + green accent)",
        "video_filters": [
            "eq=contrast=1.45:saturation=0.85:brightness=-0.05:gamma=0.95",
            "colorbalance=rs=0.05:gs=0.02:bs=0.12:rm=0.04:bm=0.08:bh=-0.06",
            "curves=all='0/0 0.35/0.30 1/1'",
            "vignette=PI/4.5",
            "unsharp=5:5:0.7",
        ],
        "bg_color": "0x101214",       # near-black charcoal
        "accent_color": "#00e07a",    # terminal green for big numbers
        "text_color": "#ffffff",
    },
    "none": {
        "label": "No grade (flat)",
        "video_filters": [],
        "bg_color": "0x14141f",
        "accent_color": "#ffc832",
        "text_color": "#ffffff",
    },
}

DEFAULT_STYLE = "dark_finance"

# Camera moves cycled across scenes (also individually selectable via --camera)
CAMERA_MOVES = ("slow_zoom", "push_in", "whip_pan")



def load_cw_stats(stats_path: Path, limit: int = 6) -> List[dict]:
    """Load big-number stats from out/cw_stats.json for on-screen overlays."""
    if not stats_path.exists():
        logger.warning("Stats file not found: %s (no on-screen numbers)", stats_path)
        return []
    try:
        with open(stats_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Could not parse %s: %s", stats_path, e)
        return []

    stats = data.get("stats", data) if isinstance(data, dict) else data
    cleaned = []
    for s in stats[:limit]:
        display = (s.get("display_text") or s.get("stat") or "").strip()
        value = str(s.get("value", "")).strip()
        if not display and not value:
            continue
        cleaned.append({"display_text": display, "value": value})
    logger.info("Loaded %d stats from %s", len(cleaned), stats_path)
    return cleaned


def grade_filter_chain(style: str) -> str:
    """Return the comma-joined ffmpeg filter chain for the chosen color grade."""
    return ",".join(STYLES[style]["video_filters"])


def scene_start_times(script: Script) -> List[float]:
    """Cumulative start times of hook + each scene + cta (the tight cut points)."""
    starts = []
    t = 0.0
    starts.append(0.0)
    t += getattr(script.hook, "duration_sec", 0.0) or 0.0
    for scene in script.scenes:
        starts.append(t)
        t += scene.duration_sec
    if script.cta:
        starts.append(t)
    return starts


def run_ffmpeg(cmd: List[str], timeout: int = 600) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.error("ffmpeg failed: %s", result.stderr[-3000:])
            return False
        return True
    except Exception as e:
        logger.error("ffmpeg exception: %s", e)
        return False


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




def ffmpeg_sfx_whoosh(duration: float = 0.5) -> List[str]:
    """Synthesized whoosh: band-limited noise with a fast swell/decay."""
    return [
        "-f", "lavfi",
        "-i", (
            "anoisesrc=d={dur}:c=pink:a=0.8,"
            "lowpass=f=1200,highpass=f=200,"
            "volume='0.05+0.85*abs(sin(2*PI*t/{dur}))':eval=frame,"
            "afade=t=in:st=0:d=0.08,afade=t=out:st={half}:d={half2}"
        ).format(dur=duration, half=round(duration * 0.55, 3), half2=round(duration * 0.45, 3)),
    ]


def ffmpeg_sfx_hit() -> List[str]:
    """Synthesized hit/impact: low sine thump with fast decay."""
    return [
        "-f", "lavfi",
        "-i", "sine=frequency=55:duration=0.5,volume='exp(-8*t)':eval=frame,lowpass=f=200,volume=1.6",
    ]


def build_sound_design(
    project_dir: Path,
    script: Script,
    music_file: Optional[Path],
    sfx_enabled: bool = True,
    music_db: float = -20.0,
) -> Optional[Path]:
    """Final audio bed: VO + sidechain-ducked music + hit on hook + whoosh on cuts.

    Returns project_dir/audio/enhanced_mix.wav (falls back to final_mix.wav).
    """
    audio_dir = project_dir / "audio"
    vo_path = audio_dir / "vo_full.wav"
    final_mix = audio_dir / "final_mix.wav"
    if not vo_path.exists():
        logger.warning("No vo_full.wav; keeping %s", final_mix)
        return final_mix if final_mix.exists() else None

    with wave.open(str(vo_path), "rb") as wf:
        vo_dur = wf.getnframes() / float(wf.getframerate())

    # Whooshes land just before every cut (skip t=0; hook gets the hit instead)
    cut_times = [t for t in scene_start_times(script)[1:] if 0.2 < t < vo_dur - 0.3]

    inputs = ["-i", str(vo_path)]
    filter_parts = ["[0:a]aformat=channel_layouts=stereo[vo]"]
    mix_labels = ["[vo]"]
    next_idx = 1

    # Ducked music: sidechain-compressed by the VO
    if music_file and music_file.exists():
        music_volume = 10 ** (music_db / 20.0)
        inputs += ["-i", str(music_file)]
        filter_parts.append(
            f"[{next_idx}:a]volume={music_volume:.3f},"
            f"aformat=channel_layouts=stereo,atrim=0:{vo_dur:.3f}[mus]"
        )
        filter_parts.append(
            f"[mus][0:a]sidechaincompress=threshold=0.03:ratio=8:"
            f"attack=20:release=350[musduck]"
        )
        mix_labels.append("[musduck]")
        next_idx += 1
    else:
        logger.info("No music file; skipping ducking")

    if sfx_enabled:
        inputs += ffmpeg_sfx_hit()
        filter_parts.append(f"[{next_idx}:a]aformat=channel_layouts=stereo,volume=0.9[sfx_hit]")
        mix_labels.append("[sfx_hit]")
        next_idx += 1

        for t in cut_times:
            inputs += ffmpeg_sfx_whoosh()
            delay_ms = max(0, int(t * 1000) - 220)  # whoosh leads the cut slightly
            filter_parts.append(
                f"[{next_idx}:a]aformat=channel_layouts=stereo,volume=0.55,"
                f"adelay={delay_ms}|{delay_ms}[sfx_w{next_idx}]"
            )
            mix_labels.append(f"[sfx_w{next_idx}]")
            next_idx += 1

    n = len(mix_labels)
    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={n}:duration=first:normalize=0,"
        f"alimiter=limit=0.95,apad=whole_dur={vo_dur:.3f}[aout]"
    )

    out_path = audio_dir / "enhanced_mix.wav"
    cmd = [FFMPEG_BIN, "-y"] + inputs + [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[aout]",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    if run_ffmpeg(cmd):
        logger.info("Sound design mixed: %s (whoosh cuts=%d)", out_path, len(cut_times))
        return out_path
    logger.warning("Sound-design mix failed; falling back to %s", final_mix)
    return final_mix if final_mix.exists() else None




def stat_drawtext_filters(
    stats: List[dict],
    scene_starts: List[float],
    scene_durs: List[float],
    style: str,
    stats_per_scene: int = 2,
) -> List[str]:
    """Big legible stat numbers (value + label) shown over scene windows."""
    if not stats:
        return []
    accent = STYLES[style]["accent_color"]
    text_color = STYLES[style]["text_color"]
    filters = []
    stat_idx = 0
    for i, start in enumerate(scene_starts):
        dur = scene_durs[i] if i < len(scene_durs) else 2.0
        for k in range(stats_per_scene):
            if stat_idx >= len(stats):
                break
            stat = stats[stat_idx]
            stat_idx += 1
            appear = start + 0.25 + k * 0.6
            show_until = start + max(dur - 0.2, 1.0)
            y_base = 260 + k * 340
            value = stat["value"].replace(":", "\\:").replace("'", "\\'")
            label = stat["display_text"].replace(":", "\\:").replace("'", "\\'")
            filters.append(
                "drawtext=text='{v}':fontcolor={accent}:fontsize=120:borderw=6:"
                "bordercolor=black:x=(w-text_w)/2:y={y}:"
                "enable='between(t,{a:.2f},{b:.2f})'".format(
                    v=value, accent=accent, y=y_base, a=appear, b=show_until
                )
            )
            filters.append(
                "drawtext=text='{l}':fontcolor={tc}:fontsize=44:borderw=4:"
                "bordercolor=black:x=(w-text_w)/2:y={y}:"
                "enable='between(t,{a:.2f},{b:.2f})'".format(
                    l=label, tc=text_color, y=y_base + 150, a=appear, b=show_until
                )
            )
    return filters


def apply_grade_and_stats(
    video_path: Path,
    output_path: Path,
    style: str,
    stats: List[dict],
    scene_starts: List[float],
    scene_durs: List[float],
) -> bool:
    """Post-pass: color grade + big stat numbers on any rendered video."""
    vf = []
    grade = grade_filter_chain(style)
    if grade:
        vf.append(grade)
    vf += stat_drawtext_filters(stats, scene_starts, scene_durs, style)

    cmd = [FFMPEG_BIN, "-y", "-i", str(video_path)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output_path),
    ]
    ok = run_ffmpeg(cmd)
    if ok:
        logger.info("Grade + stats applied (%s, %d filter ops)", style, len(vf))
    return ok


def script_to_edit_decisions(script: Script, project_dir: Path, audio_path: Path = None,
                             style: str = DEFAULT_STYLE) -> dict[str, Any]:
    """Convert our Script schema to OpenMontage edit_decisions format."""

    cuts = []

    # Hook scene â€” tight pacing: hard cut in/out, no dead air
    hook_duration = getattr(script.hook, 'duration_sec', 3.0) or 3.0
    cuts.append({
        "id": "hook",
        "source": "",
        "in_seconds": 0.0,
        "out_seconds": hook_duration,
        "type": "text_card",
        "text": script.hook.text or script.hook.first_3s,
        "visual": script.hook.visual,
        "transition_in": "cut",
        "transition_out": "cut",
        "transition_duration": 0.0,
        "camera_move": "push_in",
        "layer": "primary",
        "reason": "Scroll-stopping hook"
    })

    # Script scenes â€” cycle camera moves (slow zoom / push-in / whip-pan)
    camera_cycle = list(CAMERA_MOVES)
    for i, scene in enumerate(script.scenes):
        duration = scene.duration_sec
        is_last = i == len(script.scenes) - 1
        cuts.append({
            "id": f"scene_{i+1}",
            "source": "",
            "in_seconds": 0.0,
            "out_seconds": duration,
            "type": "generated",
            "text": scene.on_screen_text or scene.visual,
            "visual": scene.visual,
            "transition_in": "cut",
            "transition_out": "fade" if is_last else "cut",
            "transition_duration": 0.5 if is_last else 0.0,
            "camera_move": camera_cycle[i % len(camera_cycle)],
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
        "visual": "CrowdWisdomTrading free trial",
        "transition_in": "cut",
        "transition_out": "fade",
        "transition_duration": 1.0,
        "camera_move": "slow_zoom",
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
        "style": style,
        "audio": {
            "narration": {
                "segments": narration_segments
            },
            "music": {
                "asset_id": "background_music",
                "volume": 0.15,
                "fade_in_seconds": 0.5,
                "fade_out_seconds": 1.5,
                "ducking": {
                    "enabled": True,
                    "threshold_db": -30,
                    "reduction_db": 14,
                    "attack_ms": 20,
                    "release_ms": 350
                }
            },
            "sfx": [
                {"type": "hit", "asset_id": "sfx_hit", "time_seconds": 0.0},
                *[
                    {"type": "whoosh", "asset_id": f"sfx_whoosh_{i}", "time_seconds": t}
                    for i, t in enumerate([s for s in scene_start_times(script)[1:] if s > 0.2])
                ]
            ]
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
    music_path: str = None,
    style: str = DEFAULT_STYLE,
    stats_path: str = None,
    camera: str = None,
    pad_sec: float = 0.0,
    no_sfx: bool = False,
) -> Path:
    """Render script JSON to MP4 using OpenMontage video_compose with TTS."""
    if style not in STYLES:
        raise ValueError(f"Unknown style '{style}'. Choose from: {', '.join(STYLES)}")

    script_file = Path(script_path)
    if not script_file.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")

    with open(script_file, "r") as f:
        script_data = json.load(f)

    script = Script.model_validate(script_data)

    # Output path
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    output_file = out_path / f"video_{script.type}_{style}.mp4"

    # Create project directory structure
    project_dir = out_path / f"project_{script.type}"
    project_dir.mkdir(parents=True, exist_ok=True)

    # Resolve music path
    if music_path:
        music_file = Path(music_path)
    else:
        music_file = find_music_track()

    # Stats for big on-screen numbers
    stats_file = Path(stats_path) if stats_path else DEFAULT_STATS_FILE
    stats = load_cw_stats(stats_file)

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
                "cta": script.cta,
                "style": style,
                "camera": camera or "cycle: " + ", ".join(CAMERA_MOVES),
                "pad_sec": pad_sec,
                "sfx": not no_sfx,
                "stats_file": str(stats_file),
                "stats_loaded": len(stats),
            }, f, indent=2)

        edit_decisions = script_to_edit_decisions(script, project_dir, style=style)
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

    # Final audio path: sound-designed mix (ducked music + whooshes + hook hit)
    final_audio_path = build_sound_design(
        project_dir, script, music_file, sfx_enabled=not no_sfx
    ) or (project_dir / "audio" / "final_mix.wav")

    # Convert to OpenMontage format with audio path
    edit_decisions = script_to_edit_decisions(script, project_dir, final_audio_path, style=style)
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
        return render_with_ffmpeg_direct(
            script, project_dir, final_audio_path, output_file, edit_decisions,
            style=style, camera=camera, pad_sec=pad_sec,
        )

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
        logger.warning("Falling back to direct FFmpeg render")
        return render_with_ffmpeg_direct(
            script, project_dir, final_audio_path, output_file, edit_decisions,
            style=style, camera=camera, pad_sec=pad_sec,
        )

    if not output_file.exists():
        logger.warning("Render completed but output file missing: %s", output_file)
        logger.warning("Falling back to direct FFmpeg render")
        return render_with_ffmpeg_direct(
            script, project_dir, final_audio_path, output_file, edit_decisions,
            style=style, camera=camera, pad_sec=pad_sec,
        )

    # Post-pass: consistent colour grade + big stat numbers from cw_stats.json
    graded_file = output_file.with_name(output_file.stem + "_graded.mp4")
    scene_starts = scene_start_times(script)
    scene_durs = (
        [getattr(script.hook, "duration_sec", 0.0) or 0.0]
        + [s.duration_sec for s in script.scenes]
        + ([getattr(script, "cta_duration", 0.0) or 0.0] if script.cta else [])
    )
    if apply_grade_and_stats(output_file, graded_file, style, stats,
                             scene_starts, scene_durs):
        shutil.move(str(graded_file), str(output_file))
    else:
        logger.warning("Grade/stats post-pass failed; keeping engine render")

    logger.info("Render successful in %.1fs: %s", elapsed, output_file)
    return str(output_file)


def zoompan_for_move(move: str, dur: float) -> str:
    """zoompan expression for a per-scene camera move (vertical 1080x1920)."""
    frames = max(1, int(dur * FPS))
    if move == "push_in":
        return (
            f"zoompan=z='min(1+0.0009*on,1.22)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={W}x{H}:fps={FPS}"
        )
    if move == "whip_pan":
        return (
            f"zoompan=z=1.15:x='iw/2-(iw/zoom/2)-({W}*0.35)*(min(on/{max(1, int(frames*0.4))}\\,1))':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}"
        )
    # slow_zoom (default)
    return (
        f"zoompan=z='min(1+0.00035*on,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={W}x{H}:fps={FPS}"
    )


def big_scene_text_filters(
    text: str, dur: float, style: str, is_hook: bool = False
) -> List[str]:
    """Large, legible scene text centered on the card."""
    color = STYLES[style]["accent_color"] if is_hook else STYLES[style]["text_color"]
    size = 48 if is_hook else 42
    wrapped = textwrap.wrap(text, width=24 if is_hook else 28)
    escaped_lines = [
        line.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("%", "\\\\%")
        .replace("'", "\\'")
        for line in wrapped
    ]
    t = "\\n".join(escaped_lines)[:120]
    if not t:
        return []
    return [
        "drawtext=text='{t}':fontcolor={c}:fontsize={s}:borderw=8:bordercolor=black:"
        "x=(w-text_w)/2:y=(h-text_h)/2:"
        "enable='between(t\\,0.15\\,{b:.2f})'".format(
            t=t, c=color, s=size, b=max(0.3, dur - 0.15)
        )
    ]


def scene_visual_filters(visual: str, scene_index: int, style: str) -> List[str]:
    """Build lightweight visual motifs so cards communicate more than words."""
    accent = STYLES[style]["accent_color"]
    visual_lines = textwrap.wrap(visual, width=34)[:3]
    escaped = [
        line.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("%", "\\\\%")
        .replace("'", "\\'")
        for line in visual_lines
    ]
    filters = [
        "drawgrid=width=90:height=90:thickness=1:color=white@0.08",
        f"drawbox=x=70:y=230:w=940:h=1160:color={accent}@0.18:t=6",
        f"drawbox=x=70:y=230:w=18:h=1160:color={accent}:t=fill",
    ]
    if escaped:
        visual_text = "\\n".join(escaped)
        filters.append(
            "drawtext=text='{text}':fontcolor=white@0.85:fontsize=34:"
            "x=120:y=310:line_spacing=12:borderw=2:bordercolor=black"
            .format(text=visual_text)
        )

    # A compact bar-chart motif gives the cards a recognizable trading visual.
    heights = [220, 360, 280, 470, 410]
    shift = (scene_index * 37) % 90
    for bar_index, height in enumerate(heights):
        x = 150 + bar_index * 155
        y = 1030 - height
        filters.append(
            f"drawbox=x={x + shift}:y={y}:w=92:h={height}:"
            f"color={accent}@{0.42 + bar_index * 0.08:.2f}:t=fill"
        )
    filters.append(
        f"drawbox=x=130:y=1030:w=790:h=8:color={accent}:t=fill"
    )
    return filters


def render_with_ffmpeg_direct(
    script: Script,
    project_dir: Path,
    audio_path: Optional[Path],
    output_file: Path,
    edit_decisions: dict,
    style: str = DEFAULT_STYLE,
    camera: Optional[str] = None,
    pad_sec: float = 0.0,
) -> str:
    """Direct FFmpeg render: per-scene cards with camera moves (slow zoom /
    push-in / whip-pan), big text, tight VO-aligned cuts, color grade, and the
    sound-designed audio muxed in."""
    logger.info("Creating video directly with FFmpeg (style=%s, camera=%s)...", style, camera)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    work_dir = project_dir / "scenes"
    work_dir.mkdir(parents=True, exist_ok=True)

    cuts = edit_decisions.get("cuts", [])
    if not cuts:
        logger.error("No cuts in edit_decisions")
        return str(audio_path or "")

    bg_color = STYLES[style]["bg_color"]
    camera_cycle = list(CAMERA_MOVES) if camera is None else [camera]

    scene_files: List[Path] = []
    for i, cut in enumerate(cuts):
        dur = max(0.4, float(cut["out_seconds"]) - float(cut.get("in_seconds", 0.0)))
        camera = camera_cycle[i % len(camera_cycle)]
        is_hook = cut["id"] == "hook"
        is_cta = cut["id"] == "cta"

        # Tight pacing: scene lasts exactly its VO length (+ optional pad, no dead air)
        scene_dur = dur + pad_sec
        text = cut.get("text") or ""

        scene_path = work_dir / f"scene_{i:02d}.mp4"
        visual = cut.get("visual") or ""
        vf = scene_visual_filters(visual, i, style)
        vf += big_scene_text_filters(text, scene_dur, style, is_hook=is_hook or is_cta)
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i",
            f"color=c={bg_color}:size={W}x{H}:duration={scene_dur:.3f}:rate={FPS}",
            "-vf", ",".join(vf),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            str(scene_path),
        ]
        if not run_ffmpeg(cmd, timeout=180):
            logger.error("Scene %d render failed", i)
            return str(audio_path or "")
        scene_files.append(scene_path)

    # Concat scenes (hard cuts, no dead air)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in scene_files:
            f.write(f"file '{p.resolve()}'\n")
        list_file = f.name
    try:
        concat_path = work_dir / "concat.mp4"
        if not run_ffmpeg([
            FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", str(concat_path),
        ], timeout=120):
            return str(audio_path or "")
    finally:
        if os.path.exists(list_file):
            os.unlink(list_file)

    # Color grade pass + mux the sound-designed audio
    graded_path = work_dir / "graded.mp4"
    grade = grade_filter_chain(style)
    cmd = [FFMPEG_BIN, "-y", "-i", str(concat_path)]
    if audio_path and Path(audio_path).exists():
        cmd += ["-i", str(audio_path)]
    if grade:
        cmd += ["-vf", grade]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    if audio_path and Path(audio_path).exists():
        cmd += ["-map", "0:v", "-map", "1:a",
                "-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += [str(graded_path)]
    if not run_ffmpeg(cmd):
        return str(audio_path or "")

    shutil.move(str(graded_path), str(output_file))
    logger.info("Direct FFmpeg render successful: %s", output_file)
    return str(output_file)


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
    parser.add_argument(
        "--style", default=DEFAULT_STYLE, choices=sorted(STYLES),
        help="Colour grade / look (default: %s). Options: %s" % (
            DEFAULT_STYLE,
            "; ".join(f"{k} = {v['label']}" for k, v in STYLES.items()),
        )
    )
    parser.add_argument(
        "--stats", default=None,
        help=f"Path to cw_stats.json for on-screen numbers (default: {DEFAULT_STATS_FILE})"
    )
    parser.add_argument(
        "--camera", default=None, choices=list(CAMERA_MOVES),
        help="Force one camera move for all scenes (default: cycle %s)" % ", ".join(CAMERA_MOVES)
    )
    parser.add_argument(
        "--pad-sec", type=float, default=0.0,
        help="Extra seconds after each VO ends (default: 0 = cut at VO end, no dead air)"
    )
    parser.add_argument(
        "--no-sfx", action="store_true",
        help="Disable whoosh/hit sound design (music ducking stays on)"
    )
    args = parser.parse_args()

    try:
        output = render_script(
            args.script, args.out_dir, args.dry_run, args.timeout, args.music,
            style=args.style, stats_path=args.stats, camera=args.camera,
            pad_sec=args.pad_sec, no_sfx=args.no_sfx,
        )
        logger.info("Output: %s", output)
        return 0
    except Exception as e:
        logger.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
