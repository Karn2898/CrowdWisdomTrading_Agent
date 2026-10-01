#!/usr/bin/env python3
"""Generate voiceover using Piper TTS and mix with music via ffmpeg."""

import json
import logging
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

# Ensure ffmpeg is in PATH
FFMPEG_PATH = r"C:\Users\DELL\tools\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
FFMPEG_DIR = os.path.dirname(FFMPEG_PATH)
if os.path.exists(FFMPEG_PATH):
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class PiperTTS:
    """Piper TTS wrapper for generating voiceovers."""
    
    def __init__(self, model_dir: Path = None):
        self.model_dir = model_dir or Path(__file__).parent.parent / "models"
        self.model_path = self.model_dir / "en_US-lessac-medium.onnx"
        self.config_path = self.model_dir / "en_US-lessac-medium.onnx.json"
        
        if not self.model_path.exists():
            raise FileNotFoundError(f"Piper model not found: {self.model_path}")
    
    def synthesize(self, text: str, output_path: Path, speaker_id: int = 0) -> float:
        """Synthesize text to WAV file, return duration in seconds."""
        try:
            from piper import PiperVoice
        except ImportError:
            raise ImportError("piper-tts not installed. Run: pip install piper-tts")
        
        voice = PiperVoice.load(str(self.model_path), config_path=str(self.config_path))
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with wave.open(str(output_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        
        # Get duration
        with wave.open(str(output_path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            duration = frames / float(rate)
        
        logger.debug("Synthesized %.2fs: %s", duration, text[:50])
        return duration
    
    def synthesize_scenes(self, scenes: List[dict], output_dir: Path) -> List[dict]:
        """Synthesize VO for each scene, return scenes with audio paths and durations."""
        output_dir.mkdir(parents=True, exist_ok=True)
        results = []
        
        for i, scene in enumerate(scenes):
            vo_text = scene.get("vo", "").strip()
            if not vo_text:
                logger.warning("Scene %d has empty VO, skipping", i)
                results.append({**scene, "audio_path": None, "vo_duration": 0.0})
                continue
            
            audio_path = output_dir / f"scene_{i:02d}_vo.wav"
            duration = self.synthesize(vo_text, audio_path)
            results.append({**scene, "audio_path": str(audio_path), "vo_duration": duration})
        
        return results


def concatenate_audio(audio_paths: List[str], output_path: Path) -> float:
    """Concatenate multiple WAV files using ffmpeg, return total duration."""
    if not audio_paths:
        return 0.0
    
    # Filter out None paths
    valid_paths = [p for p in audio_paths if p and Path(p).exists()]
    if not valid_paths:
        return 0.0
    
    # Create concat list file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in valid_paths:
            f.write(f"file '{Path(p).resolve()}'\n")
        list_file = f.name
    
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("ffmpeg concat failed: %s", result.stderr)
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr}")
        
        # Get total duration
        total_duration = 0.0
        for p in valid_paths:
            with wave.open(p, "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                total_duration += frames / float(rate)
        
        return total_duration
    finally:
        if os.path.exists(list_file):
            os.unlink(list_file)


def mix_music_under_vo(
    vo_path: Path,
    music_path: Path,
    output_path: Path,
    music_db: float = -18.0,
    fade_in: float = 2.0,
    fade_out: float = 2.0
) -> float:
    """Mix music under VO at specified dB level with fade in/out."""
    
    if not music_path.exists():
        logger.warning("Music file not found: %s, copying VO only", music_path)
        import shutil
        shutil.copy2(vo_path, output_path)
        with wave.open(str(vo_path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            return frames / float(rate)
    
    # Get VO duration for fade out timing
    with wave.open(str(vo_path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        vo_duration = frames / float(rate)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Build ffmpeg filter:
    # - VO at full volume (ensure stereo)
    # - Music at music_db (e.g., -18 dB = 0.126 amplitude) with fade in/out
    # - Mix with amix (ensure both are stereo)
    
    music_volume = 10 ** (music_db / 20.0)  # Convert dB to amplitude
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(vo_path),
        "-i", str(music_path),
        "-filter_complex",
        f"[0:a]aformat=channel_layouts=stereo[vo];"
        f"[1:a]volume={music_volume:.3f},afade=t=in:st=0:d={fade_in},afade=t=out:st={vo_duration - fade_out}:d={fade_out},aformat=channel_layouts=stereo[music];"
        f"[vo][music]amix=inputs=2:duration=first:dropout_transition=0[out]",
        "-map", "[out]",
        "-c:a", "pcm_s16le",
        str(output_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg mix failed: %s", result.stderr)
        raise RuntimeError(f"ffmpeg mix failed: {result.stderr}")
    
    return vo_duration


def generate_voiceover(
    script_data: dict,
    project_dir: Path,
    music_path: Path = None
) -> Tuple[float, List[dict]]:
    """Generate full voiceover for script, return (total_duration, scenes_with_audio)."""
    
    tts = PiperTTS()
    audio_dir = project_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    
    # Get scenes from script
    scenes = script_data.get("scenes", [])
    
    # Also include hook and CTA as scenes for VO
    all_segments = []
    
    # Hook
    hook_text = script_data.get("hook", {}).get("first_3s", "").strip()
    if hook_text:
        all_segments.append({"vo": hook_text, "type": "hook", "duration_sec": 3.0})
    
    # Scenes
    for scene in scenes:
        all_segments.append({"vo": scene.get("vo", "").strip(), "type": "scene", **scene})
    
    # CTA
    cta_text = script_data.get("cta", "").strip()
    if cta_text:
        all_segments.append({"vo": cta_text, "type": "cta", "duration_sec": 3.0})
    
    # Synthesize each segment
    logger.info("Generating VO for %d segments", len(all_segments))
    segments_with_audio = tts.synthesize_scenes(all_segments, audio_dir)
    
    # Concatenate all VO
    vo_paths = [s["audio_path"] for s in segments_with_audio if s.get("audio_path")]
    concat_vo_path = audio_dir / "vo_full.wav"
    total_vo_duration = concatenate_audio(vo_paths, concat_vo_path)
    
    logger.info("Total VO duration: %.2fs", total_vo_duration)
    
    # Mix with music if provided
    final_audio_path = audio_dir / "final_mix.wav"
    if music_path and music_path.exists():
        logger.info("Mixing music: %s", music_path)
        mix_music_under_vo(concat_vo_path, music_path, final_audio_path)
    else:
        import shutil
        shutil.copy2(concat_vo_path, final_audio_path)
        logger.info("No music provided, using VO only")
    
    return total_vo_duration, segments_with_audio


def adjust_scene_durations(
    script_data: dict,
    segments_with_audio: List[dict],
    target_total: float = 30.0,
    max_total: float = 60.0
) -> dict:
    """Adjust scene durations to match actual VO while keeping total in range."""
    
    # Calculate actual VO duration per scene
    scene_durations = []
    for seg in segments_with_audio:
        if seg.get("type") in ("scene", "hook", "cta"):
            dur = seg.get("vo_duration", seg.get("duration_sec", 0))
            scene_durations.append(dur)
    
    total_vo = sum(scene_durations)
    
    if total_vo < target_total:
        # Need to stretch - distribute extra time proportionally
        scale = target_total / total_vo
        logger.info("VO too short (%.1fs), scaling by %.2fx to reach %.1fs", total_vo, scale, target_total)
    elif total_vo > max_total:
        # Need to compress
        scale = max_total / total_vo
        logger.warning("VO too long (%.1fs), scaling by %.2fx to fit %.1fs", total_vo, scale, max_total)
    else:
        scale = 1.0
        logger.info("VO duration %.1fs within target range", total_vo)
    
    # Apply scaled durations back to script
    adjusted = script_data.copy()
    adjusted_scenes = []
    seg_idx = 0
    
    # Hook duration
    hook_dur = 0.0
    if script_data.get("hook"):
        hook_dur = segments_with_audio[seg_idx].get("vo_duration", 3.0) * scale
        adjusted["hook"] = {**adjusted["hook"], "duration_sec": hook_dur}
        seg_idx += 1
    
    for scene in script_data.get("scenes", []):
        if seg_idx < len(segments_with_audio):
            vo_dur = segments_with_audio[seg_idx].get("vo_duration", scene.get("duration_sec", 0)) * scale
            adjusted_scenes.append({**scene, "duration_sec": vo_dur})
            seg_idx += 1
        else:
            adjusted_scenes.append(scene)
    
    # CTA
    cta_dur = 0.0
    if script_data.get("cta") and seg_idx < len(segments_with_audio):
        cta_dur = segments_with_audio[seg_idx].get("vo_duration", 3.0) * scale
        adjusted["cta_duration"] = cta_dur
    
    adjusted["scenes"] = adjusted_scenes
    # total_runtime includes hook + scenes + cta
    adjusted["total_runtime"] = hook_dur + sum(s.get("duration_sec", 0) for s in adjusted_scenes) + cta_dur
    
    return adjusted


def main() -> int:
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate VO with Piper TTS")
    parser.add_argument("script", help="Path to script JSON")
    parser.add_argument("--project-dir", default="out/videos/project")
    parser.add_argument("--music", help="Path to music file")
    parser.add_argument("--output", help="Output audio path")
    args = parser.parse_args()
    
    script_file = Path(args.script)
    if not script_file.exists():
        logger.error("Script not found: %s", args.script)
        return 1
    
    with open(script_file) as f:
        script_data = json.load(f)
    
    project_dir = Path(args.project_dir)
    music_path = Path(args.music) if args.music else None
    
    try:
        total_dur, segments = generate_voiceover(script_data, project_dir, music_path)
        logger.info("Voiceover generated: %.2fs total", total_dur)
        
        # Adjust script durations
        adjusted = adjust_scene_durations(script_data, segments)
        
        # Write adjusted script
        out_script = project_dir / "script_adjusted.json"
        with open(out_script, "w") as f:
            json.dump(adjusted, f, indent=2)
        logger.info("Adjusted script written to %s", out_script)
        
        return 0
    except Exception as e:
        logger.error("Failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())