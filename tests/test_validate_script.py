#!/usr/bin/env python3
"""Tests for validate_script.py"""

import json
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.validate_script import validate_script, validate_file


class TestValidateScript:
    """Tests for script validation."""

    def base_script(self) -> dict:
        """Return a base valid script - caller must ensure total_runtime matches scene durations."""
        return {
            "type": "pain",
            "hook": {
                "visual": "Red trading account on phone",
                "first_3s": "Your account just dropped 20% in one week",
                "text": "Account -20%",
                "sfx": "Alarm sound"
            },
            "scenes": [
                {
                    "visual": "Trader looking stressed at screen",
                    "camera": "Close-up, handheld",
                    "vo": "You watched your portfolio bleed while the market ripped higher",
                    "on_screen_text": "Missed the move",
                    "duration_sec": 8.0,
                    "music": "Tense, low drone"
                },
                {
                    "visual": "Chart showing missed breakout",
                    "camera": "Screen record, zoom",
                    "vo": "The setup was there but you hesitated and the signal vanished",
                    "on_screen_text": "Hesitation costs",
                    "duration_sec": 7.0,
                    "music": "Tense, building"
                },
                {
                    "visual": "CrowdWisdomTrading alert on phone",
                    "camera": "Static, phone screen",
                    "vo": "CrowdWisdomTrading gives you the signal before the move",
                    "on_screen_text": "Get the signal",
                    "duration_sec": 7.0,
                    "music": "Hopeful, rising"
                },
                {
                    "visual": "Win rate 73 percent on screen",
                    "camera": "Static, graphics",
                    "vo": "Seventy three percent win rate across two thousand signals",
                    "on_screen_text": "73% win rate",
                    "duration_sec": 5.0,
                    "music": "Confident, steady"
                },
                {
                    "visual": "Join button, link in bio",
                    "camera": "Static, screen record",
                    "vo": "Tap the link in bio and start getting winning signals today",
                    "on_screen_text": "Link in bio",
                    "duration_sec": 5.0,
                    "music": "Upbeat, resolved"
                }
            ],
            "cta": "Tap link in bio for free trial",
            "total_runtime": 32.0  # 8+7+7+5+5 = 32
        }

    def test_valid_passes(self):
        """Valid script should pass validation."""
        script = self.base_script()
        errors = validate_script(script)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_missing_hook(self):
        """Missing hook should fail."""
        script = self.base_script()
        del script["hook"]
        errors = validate_script(script)
        # Pydantic will catch missing required field first
        assert any("hook" in e.lower() for e in errors)

    def test_empty_hook_first_3s(self):
        """Empty hook.first_3s should fail."""
        script = self.base_script()
        script["hook"]["first_3s"] = ""
        errors = validate_script(script)
        assert any("first_3s is missing or empty" in e for e in errors)

    def test_total_runtime_too_short(self):
        """Total runtime < 30s (computed from scenes) should fail."""
        script = self.base_script()
        # Reduce scene durations to make total < 30
        for scene in script["scenes"]:
            scene["duration_sec"] = 4.0  # 5 * 4 = 20
        script["total_runtime"] = 20.0
        errors = validate_script(script)
        assert any("outside 30-60s range" in e or "30-60" in e for e in errors)

    def test_total_runtime_too_long(self):
        """Total runtime > 60s (computed from scenes) should fail."""
        script = self.base_script()
        # Increase scene durations to make total > 60
        for scene in script["scenes"]:
            scene["duration_sec"] = 15.0  # 5 * 15 = 75
        script["total_runtime"] = 75.0
        errors = validate_script(script)
        assert any("outside 30-60s range" in e or "30-60" in e for e in errors)

    def test_scene_count_too_few(self):
        """Scene count < 5 should fail."""
        script = self.base_script()
        script["scenes"] = script["scenes"][:4]  # Only 4 scenes
        script["total_runtime"] = sum(s["duration_sec"] for s in script["scenes"])
        errors = validate_script(script)
        assert any("at least 5" in e or "5-7" in e for e in errors)

    def test_scene_count_too_many(self):
        """Scene count > 7 should fail."""
        script = self.base_script()
        extra_scene = script["scenes"][0].copy()
        script["scenes"] = script["scenes"] + [extra_scene] * 4  # 9 scenes
        script["total_runtime"] = sum(s["duration_sec"] for s in script["scenes"])
        errors = validate_script(script)
        assert any("at most 7" in e or "5-7" in e for e in errors)

    def test_empty_vo(self):
        """Empty VO should fail."""
        script = self.base_script()
        script["scenes"][0]["vo"] = ""
        errors = validate_script(script)
        assert any("VO is empty" in e for e in errors)

    def test_vo_too_long_for_duration(self):
        """VO word count exceeding duration at 3 words/sec should fail."""
        script = self.base_script()
        # 50 words in 8 seconds - max is 24 words at 3 words/sec
        script["scenes"][0]["vo"] = " ".join(["word"] * 50)
        script["scenes"][0]["duration_sec"] = 8.0  # Keep duration same
        # total_runtime still valid (8+7+7+5+5 = 32)
        errors = validate_script(script)
        assert any("VO has 50 words" in e for e in errors)

    def test_negative_duration(self):
        """Negative duration should fail."""
        script = self.base_script()
        # Make scene 1 negative but adjust others to keep total valid
        script["scenes"][0]["duration_sec"] = -1.0
        script["scenes"][1]["duration_sec"] = 20.0  # Compensate to keep total ~39
        script["total_runtime"] = sum(s["duration_sec"] for s in script["scenes"])
        errors = validate_script(script)
        assert any("positive" in e.lower() for e in errors)

    def test_zero_duration(self):
        """Zero duration should fail."""
        script = self.base_script()
        script["scenes"][0]["duration_sec"] = 0.0
        script["scenes"][1]["duration_sec"] = 20.0  # Compensate to keep total ~39
        script["total_runtime"] = sum(s["duration_sec"] for s in script["scenes"])
        errors = validate_script(script)
        assert any("positive" in e.lower() for e in errors)


class TestValidateFile:
    """Tests for file validation."""

    def test_file_not_found(self):
        """Non-existent file should return error."""
        errors = validate_file(Path("nonexistent.json"))
        assert any("File not found" in e for e in errors)

    def test_invalid_json(self, tmp_path):
        """Invalid JSON should return error."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{ invalid json")
        errors = validate_file(bad_file)
        assert any("Invalid JSON" in e for e in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])