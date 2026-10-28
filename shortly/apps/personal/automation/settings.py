"""Configuration that must never become part of the team product surface."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class PersonalAutomationSettings:
    state_dir: Path
    artifact_dir: Path
    openai_image_model: str = "gpt-image-2"
    google_video_model: str = "gemini-omni-1.1-flash"
    image_budget_per_video_usd: float = 1.0
    video_budget_per_video_usd: float = 5.0
    daily_budget_usd: float = 10.0
    monthly_budget_usd: float = 50.0
    image_estimated_cost_usd: float = 0.05
    video_estimated_cost_usd: float = 0.4
    candidate_count: int = 1
    pixabay_api_key: str = ""
    pexels_api_key: str = ""

    @property
    def database_path(self) -> Path:
        return self.state_dir / "automation.sqlite3"

    @property
    def assets_dir(self) -> Path:
        return self.artifact_dir / "generated-assets"

    @classmethod
    def load(cls, *, state_dir: Path, artifact_dir: Path):
        return cls(
            state_dir=state_dir,
            artifact_dir=artifact_dir,
            openai_image_model=os.environ.get(
                "SHORTLY_IMAGE_MODEL", "gpt-image-2"
            ).strip()
            or "gpt-image-2",
            google_video_model=os.environ.get(
                "SHORTLY_VIDEO_MODEL", "gemini-omni-1.1-flash"
            ).strip()
            or "gemini-omni-1.1-flash",
            image_budget_per_video_usd=_float(
                "SHORTLY_IMAGE_BUDGET_PER_VIDEO_USD", 1.0
            ),
            video_budget_per_video_usd=_float(
                "SHORTLY_VIDEO_BUDGET_PER_VIDEO_USD", 5.0
            ),
            daily_budget_usd=_float("SHORTLY_DAILY_ASSET_BUDGET_USD", 10.0),
            monthly_budget_usd=_float(
                "SHORTLY_MONTHLY_ASSET_BUDGET_USD", 50.0
            ),
            image_estimated_cost_usd=_float(
                "SHORTLY_IMAGE_ESTIMATED_COST_USD", 0.05
            ),
            video_estimated_cost_usd=_float(
                "SHORTLY_VIDEO_ESTIMATED_COST_USD", 0.4
            ),
            candidate_count=max(1, _int("SHORTLY_ASSET_CANDIDATES", 1)),
            pixabay_api_key=os.environ.get("PIXABAY_API_KEY", "").strip(),
            pexels_api_key=os.environ.get("PEXELS_API_KEY", "").strip(),
        )
