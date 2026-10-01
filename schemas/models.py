"""Pydantic v2 models for CrowdWisdomTrading."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator


class Ad(BaseModel):
    """Advertisement model with metadata and computed fields."""

    id: str
    page_name: str
    ad_text: str
    headline: str | None = None
    cta: str | None = None
    media_type: Literal["video", "image", "other"]
    start_date: date
    end_date: date | None = None
    is_active: bool
    ad_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @computed_field
    @property
    def days_running(self) -> int:
        """Compute days running from start_date to end_date or today."""
        end = self.end_date or date.today()
        return (end - self.start_date).days


class Insight(BaseModel):
    """Insight extracted from ads."""

    pain: str
    concept: str
    hook: str
    icp: str
    source_ad_ids: list[str] = Field(default_factory=list)


class InsightsReport(BaseModel):
    """Report containing multiple insights and aggregated data."""

    insights: list[Insight]
    top_pains: list[str]
    top_angles: list[str]
    top_hooks: list[str]


class Hook(BaseModel):
    """Hook component of a script."""

    visual: str
    first_3s: str
    text: str
    sfx: str
    duration_sec: float = 3.0


class Scene(BaseModel):
    """Individual scene in a script."""

    visual: str
    camera: str
    vo: str
    on_screen_text: str
    duration_sec: float
    music: str


class Script(BaseModel):
    """Script model with validation rules."""

    type: Literal["pain", "data", "solution"]
    hook: Hook
    scenes: list[Scene] = Field(min_length=5, max_length=7)
    cta: str
    cta_duration: float = 3.0

    @computed_field
    @property
    def total_runtime(self) -> float:
        """Total runtime of the script in seconds (hook + scenes + CTA)."""
        return self.hook.duration_sec + sum(scene.duration_sec for scene in self.scenes) + self.cta_duration

    @model_validator(mode="after")
    def validate_script(self) -> Script:
        """Validate script constraints."""
        if not self.hook:
            raise ValueError("hook must be present")

        scene_count = len(self.scenes)
        if scene_count < 5 or scene_count > 7:
            raise ValueError(f"scene count must be 5-7, got {scene_count}")

        total_duration = self.total_runtime
        # Use small epsilon for floating point comparison
        if total_duration < 30 - 1e-9 or total_duration > 60 + 1e-9:
            raise ValueError(f"total duration must be 30-60s, got {total_duration:.1f}s")

        return self