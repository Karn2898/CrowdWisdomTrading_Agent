#!/usr/bin/env python3
"""Pick the hero video: ffprobe runtime + LLM score hooks/clarity/credibility/CTA."""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SCRIPTS_DIR = PROJECT_ROOT / "out" / "scripts"
VIDEOS_DIR = PROJECT_ROOT / "out" / "videos"
HERO_OUT = PROJECT_ROOT / "out" / "hero.mp4"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Map script type -> rendered video file
VIDEO_FILES = {
    "pain": VIDEOS_DIR / "video_pain.mp4",
    "data": VIDEOS_DIR / "video_data.mp4",
    "solution": VIDEOS_DIR / "video_solution.mp4",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def resolve_ffprobe() -> str:
    """Resolve ffprobe from an explicit override or PATH."""
    configured_path = os.getenv("FFPROBE_PATH")
    if configured_path:
        ffprobe_path = Path(configured_path)
        if ffprobe_path.exists():
            return str(ffprobe_path)
        raise FileNotFoundError(f"FFPROBE_PATH is set but does not exist: {ffprobe_path}")

    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        return ffprobe_path
    raise FileNotFoundError(
        "ffprobe not found. Install FFmpeg and add ffprobe to PATH, or set FFPROBE_PATH."
    )


def ffprobe_duration(video_path: Path) -> float | None:
    """Return video duration in seconds via ffprobe, or None on failure."""
    try:
        ffprobe = resolve_ffprobe()
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError) as e:
        logger.warning("ffprobe failed for %s: %s", video_path.name, e)
    return None


def extract_hook(script: dict) -> str:
    """Extract the hook line from a script dict."""
    hook = script.get("hook", {})
    if isinstance(hook, dict):
        return hook.get("first_3s", "") or ""
    if isinstance(hook, str):
        return hook
    return ""


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def score_scripts_with_llm(candidates: list[dict]) -> dict:
    """Ask the LLM to score each script on hook strength, clarity, data credibility, CTA."""
    system_prompt = (
        "You are a short-form video ad judge. Score each candidate video script on four "
        "dimensions, each 0-10: hook_strength (scroll-stopping first 3s), clarity (is the "
        "message instantly understandable), data_credibility (specific, believable numbers "
        "backed by real stats), and cta (strength of the call to action). Return JSON only: "
        '{"scores": [{"type": "...", "hook_strength": n, "clarity": n, "data_credibility": n, '
        '"cta": n, "rationale": "one line"}]}'
    )

    candidates_text = json.dumps(
        [
            {
                "type": c["type"],
                "hook": c["hook"],
                "script": c["script_text"],
                "cta": c["cta"],
            }
            for c in candidates
        ],
        indent=2,
    )
    user_prompt = (
        "Score these three video ad scripts for CrowdWisdomTrading. "
        "The hooks are the first 3 seconds of narration.\n\n" + candidates_text
    )

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 2000,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
    response.raise_for_status()
    content = strip_code_fences(response.json()["choices"][0]["message"]["content"])
    data = json.loads(content)

    scores = {}
    for item in data.get("scores", []):
        stype = item.get("type")
        if stype:
            scores[stype] = item
    return scores


def score_scripts_heuristically(candidates: list[dict]) -> dict:
    """Fallback scorer when OpenRouter is unavailable."""
    scores: dict[str, dict] = {}
    for c in candidates:
        hook_words = len(str(c.get("hook", "")).split())
        cta_words = len(str(c.get("cta", "")).split())
        scene_words = len(str(c.get("script_text", "")).split())
        duration = c.get("duration")
        duration_score = 5.0
        if isinstance(duration, (int, float)) and duration > 0:
            duration_score = max(0.0, 10.0 - abs(duration - 45.0) / 4.0)

        hook_strength = min(10.0, hook_words * 1.5)
        clarity = min(10.0, 4.0 + min(scene_words / 25.0, 6.0))
        data_credibility = min(10.0, 3.0 + min(scene_words / 30.0, 7.0))
        cta = min(10.0, 2.0 + cta_words * 1.2)
        total = round(duration_score + hook_strength + clarity + data_credibility + cta, 2)

        scores[c["type"]] = {
            "hook_strength": round(hook_strength, 2),
            "clarity": round(clarity, 2),
            "data_credibility": round(data_credibility, 2),
            "cta": round(cta, 2),
            "rationale": "Fallback heuristic: duration/hook/script/CTA balance",
            "_total": total,
        }
    return scores


def main() -> int:
    parser = argparse.ArgumentParser(description="Pick the hero video via ffprobe + LLM scoring")
    parser.add_argument("--dry-run", action="store_true", help="Print ranking, do not copy file")
    args = parser.parse_args()

    script_files = {
        "pain": SCRIPTS_DIR / "script_pain.json",
        "data": SCRIPTS_DIR / "script_data.json",
        "solution": SCRIPTS_DIR / "script_solution.json",
    }

    candidates = []
    for stype, sfile in script_files.items():
        if not sfile.exists():
            logger.warning("Missing script: %s", sfile)
            continue
        with open(sfile, encoding="utf-8") as f:
            script = json.load(f)

        video = VIDEO_FILES.get(stype)
        if not video or not video.exists():
            logger.warning("Missing video for %s: %s", stype, video)
            continue

        duration = ffprobe_duration(video)
        if duration is None:
            logger.warning("Could not probe runtime for %s", video.name)

        candidates.append(
            {
                "type": stype,
                "hook": extract_hook(script),
                "cta": script.get("cta", ""),
                "script_text": script.get("scenes") and " ".join(
                    s.get("vo", "") for s in script["scenes"]
                ) or "",
                "video_path": video,
                "duration": duration,
            }
        )

    if not candidates:
        logger.error("No candidate videos found")
        return 1

    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not found in environment")
        return 1

    try:
        scores = score_scripts_with_llm(candidates)
        scoring_mode = "llm"
    except Exception as e:
        logger.warning("LLM scoring failed, using heuristic fallback: %s", e)
        scores = score_scripts_heuristically(candidates)
        scoring_mode = "heuristic"

    # Combine LLM dims into a total (equal weight)
    for c in candidates:
        s = scores.get(c["type"], {})
        dims = {
            "hook_strength": float(s.get("hook_strength", 0)),
            "clarity": float(s.get("clarity", 0)),
            "data_credibility": float(s.get("data_credibility", 0)),
            "cta": float(s.get("cta", 0)),
        }
        c["dims"] = dims
        c["total"] = sum(dims.values())
        c["rationale"] = s.get("rationale", "")

    if scoring_mode == "heuristic":
        for c in candidates:
            c["total"] = float(scores.get(c["type"], {}).get("_total", 0.0))
    candidates.sort(key=lambda x: x["total"], reverse=True)

    print("\n=== HERO VIDEO RANKING ===")
    print(f"Scoring mode: {scoring_mode}")
    for rank, c in enumerate(candidates, 1):
        d = c["dims"]
        dur = f"{c['duration']:.1f}s" if c["duration"] is not None else "unknown"
        print(f"\n#{rank} {c['type']}  (total {c['total']:.0f}/40)  runtime: {dur}")
        print(f"    hook: {c['hook'][:70]}")
        print(f"    hook_strength={d['hook_strength']:.0f} clarity={d['clarity']:.0f} "
              f"data_credibility={d['data_credibility']:.0f} cta={d['cta']:.0f}")
        if c["rationale"]:
            print(f"    judge: {c['rationale']}")

    winner = candidates[0]
    print(f"\nWINNER: {winner['type']} -> {winner['video_path']}")

    if args.dry_run:
        print("(dry run: not copying)")
        return 0

    HERO_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(winner["video_path"], HERO_OUT)
    print(f"Copied hero video to {HERO_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
