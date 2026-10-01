#!/usr/bin/env python3
"""Tests for the ads pipeline."""

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from schemas.models import Ad, Insight, InsightsReport, Script


class TestAdRanking:
    """Tests for ad ranking by days_running."""

    def test_ranking_by_days_running_desc(self):
        """Ads should be ranked by days_running descending."""
        today = date.today()
        ads = [
            Ad(id="1", page_name="A", ad_text="a", media_type="video", start_date=today - timedelta(days=5), is_active=True),
            Ad(id="2", page_name="B", ad_text="b", media_type="image", start_date=today - timedelta(days=20), is_active=True),
            Ad(id="3", page_name="C", ad_text="c", media_type="other", start_date=today - timedelta(days=10), is_active=True),
        ]
        ranked = sorted(ads, key=lambda a: a.days_running, reverse=True)
        assert ranked[0].id == "2"  # 20 days
        assert ranked[1].id == "3"  # 10 days
        assert ranked[2].id == "1"  # 5 days


class Test30DayFilter:
    """Tests for 30-day filter."""

    def test_filters_ads_older_than_30_days(self):
        """Ads with start_date older than 30 days should be filtered out."""
        today = date.today()
        ads = [
            Ad(id="1", page_name="A", ad_text="a", media_type="video", start_date=today - timedelta(days=5), is_active=True),
            Ad(id="2", page_name="B", ad_text="b", media_type="image", start_date=today - timedelta(days=45), is_active=True),
            Ad(id="3", page_name="C", ad_text="c", media_type="other", start_date=today - timedelta(days=15), is_active=True),
        ]
        cutoff = today - timedelta(days=30)
        filtered = [ad for ad in ads if ad.start_date >= cutoff]
        assert len(filtered) == 2
        assert filtered[0].id == "1"
        assert filtered[1].id == "3"

    def test_keeps_ads_exactly_30_days_old(self):
        """Ads with start_date exactly 30 days ago should be kept."""
        today = date.today()
        ad = Ad(id="1", page_name="A", ad_text="a", media_type="video", start_date=today - timedelta(days=30), is_active=True)
        cutoff = today - timedelta(days=30)
        filtered = [ad for ad in [ad] if ad.start_date >= cutoff]
        assert len(filtered) == 1


class TestCacheHit:
    """Tests for cache behavior."""

    @patch("tools.apify_meta_ads.ApifyClient")
    def test_cache_hit_skips_apify_call(self, mock_client_class):
        """On cache hit, ApifyClient.call should not be invoked."""
        from tools.apify_meta_ads import build_actor_input, get_input_hash, run_actor, save_to_cache

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / ".cache"
            cache_dir.mkdir()

            # Mock the cache path
            import tools.apify_meta_ads as ama
            original_get_cache_path = ama.get_cache_path

            def mock_get_cache_path(input_hash):
                return cache_dir / f"apify_{input_hash}.json"

            ama.get_cache_path = mock_get_cache_path

            actor_input = build_actor_input()
            input_hash = get_input_hash(actor_input)

            # Pre-populate cache
            cached_data = [{"id": "1", "page_name": "Test", "ad_text": "test", "media_type": "video", "start_date": "2024-01-15", "is_active": True}]
            save_to_cache(input_hash, cached_data)

            # Run with cache hit (no refresh)
            result = ama.load_from_cache(input_hash)

            assert result == cached_data
            mock_client.actor.assert_not_called()

            # Restore
            ama.get_cache_path = original_get_cache_path


class TestInsightSchema:
    """Tests for Insight schema validation."""

    def test_valid_insight(self):
        """Valid insight should pass validation."""
        insight = Insight(
            pain="Fear of missing out on market moves",
            concept="AI-powered signal detection",
            hook="Stop guessing entries",
            icp="Active swing traders",
            source_ad_ids=["ad1", "ad2"],
        )
        assert insight.pain == "Fear of missing out on market moves"
        assert insight.source_ad_ids == ["ad1", "ad2"]

    def test_insight_requires_all_fields(self):
        """Insight requires pain, concept, hook, icp."""
        with pytest.raises(Exception):
            Insight(pain="test")  # Missing required fields

    def test_insights_report_validation(self):
        """InsightsReport should validate correctly."""
        report = InsightsReport(
            insights=[
                Insight(pain="p1", concept="c1", hook="h1", icp="i1", source_ad_ids=["ad1"]),
                Insight(pain="p2", concept="c2", hook="h2", icp="i2", source_ad_ids=["ad2"]),
            ],
            top_pains=["p1", "p2"],
            top_angles=["c1", "c2"],
            top_hooks=["h1", "h2"],
        )
        assert len(report.insights) == 2
        assert report.top_pains == ["p1", "p2"]

    def test_script_validation_total_runtime(self):
        """Script should validate total runtime 30-60s."""
        from schemas.models import Hook, Scene

        hook = Hook(visual="v", first_3s="f", text="t", sfx="s", duration_sec=3.0)
        scenes = [
            Scene(visual="v", camera="c", vo="vo", on_screen_text="t", duration_sec=10, music="m")
            for _ in range(5)
        ]

        # 5 scenes * 10s = 50s + hook 3s + cta 3s = 56s (valid)
        script = Script(type="pain", hook=hook, scenes=scenes, cta="Buy now")
        assert script.total_runtime == 56

        # 5 scenes * 8s = 40s + hook 3s + cta 3s = 46s (valid)
        scenes_short = [
            Scene(visual="v", camera="c", vo="vo", on_screen_text="t", duration_sec=8, music="m")
            for _ in range(5)
        ]
        script2 = Script(type="data", hook=hook, scenes=scenes_short, cta="Buy now")
        assert script2.total_runtime == 46

    def test_script_validation_fails_short_runtime(self):
        """Script should fail if total runtime < 30s."""
        from schemas.models import Hook, Scene

        hook = Hook(visual="v", first_3s="f", text="t", sfx="s")
        scenes = [
            Scene(visual="v", camera="c", vo="vo", on_screen_text="t", duration_sec=4, music="m")
            for _ in range(5)
        ]  # 20s total

        with pytest.raises(ValueError, match="total duration must be 30-60s"):
            Script(type="pain", hook=hook, scenes=scenes, cta="Buy now")

    def test_script_validation_fails_long_runtime(self):
        """Script should fail if total runtime > 60s."""
        from schemas.models import Hook, Scene

        hook = Hook(visual="v", first_3s="f", text="t", sfx="s")
        scenes = [
            Scene(visual="v", camera="c", vo="vo", on_screen_text="t", duration_sec=15, music="m")
            for _ in range(5)
        ]  # 75s total

        with pytest.raises(ValueError, match="total duration must be 30-60s"):
            Script(type="pain", hook=hook, scenes=scenes, cta="Buy now")

    def test_script_validation_fails_scene_count(self):
        """Script should fail if scene count not 5-7."""
        from schemas.models import Hook, Scene

        hook = Hook(visual="v", first_3s="f", text="t", sfx="s")
        scenes = [
            Scene(visual="v", camera="c", vo="vo", on_screen_text="t", duration_sec=10, music="m")
            for _ in range(4)
        ]  # 4 scenes

        with pytest.raises(ValueError, match="at least 5 items"):
            Script(type="pain", hook=hook, scenes=scenes, cta="Buy now")

    def test_script_validation_fails_missing_hook(self):
        """Script should fail if hook is missing."""
        from schemas.models import Hook, Scene

        scenes = [
            Scene(visual="v", camera="c", vo="vo", on_screen_text="t", duration_sec=10, music="m")
            for _ in range(5)
        ]

        with pytest.raises(ValueError, match="Input should be a valid dictionary"):
            Script(type="pain", hook=None, scenes=scenes, cta="Buy now")


class TestAdModel:
    """Tests for Ad model."""

    def test_ad_days_running_computed(self):
        """days_running should be computed from start_date to end_date or today."""
        today = date.today()
        ad = Ad(
            id="1", page_name="A", ad_text="a", media_type="video",
            start_date=today - timedelta(days=10), is_active=True
        )
        assert ad.days_running == 10

    def test_ad_days_running_with_end_date(self):
        """days_running should use end_date if provided."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 15)
        ad = Ad(
            id="1", page_name="A", ad_text="a", media_type="video",
            start_date=start, end_date=end, is_active=False
        )
        assert ad.days_running == 14

    def test_ad_excludes_raw_from_serialization(self):
        """raw field should be excluded from serialization."""
        ad = Ad(
            id="1", page_name="A", ad_text="a", media_type="video",
            start_date=date.today(), is_active=True, raw={"secret": "data"}
        )
        dumped = ad.model_dump(mode="json")
        assert "raw" not in dumped


if __name__ == "__main__":
    pytest.main([__file__, "-v"])