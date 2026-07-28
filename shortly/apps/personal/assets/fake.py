"""Deterministic no-cost providers for tests and local dry runs."""

from __future__ import annotations

import base64

from ....core.domain.assets import MediaKind
from ....core.ports.assets import (
    GeneratedAsset,
    ImageGenerationRequest,
    VideoGenerationRequest,
    VideoSubmission,
)


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeImageProvider:
    name = "fake"

    def __init__(self, *, cost_usd: float = 0.0, data: bytes = _PNG_1X1):
        self.cost_usd = cost_usd
        self.data = data
        self.calls = 0

    def generate(self, request: ImageGenerationRequest) -> GeneratedAsset:
        self.calls += 1
        return GeneratedAsset(
            data=self.data,
            mime_type="image/png",
            provider=self.name,
            model=request.model,
            media_kind=MediaKind.IMAGE,
            external_id=f"fake-image-{self.calls}",
            cost_usd=self.cost_usd,
        )


class FakeVideoProvider:
    name = "fake"

    def __init__(self, *, cost_usd: float = 0.0, data: bytes = b"fake-mp4"):
        self.cost_usd = cost_usd
        self.data = data
        self.calls = 0

    def submit(self, request: VideoGenerationRequest) -> VideoSubmission:
        self.calls += 1
        asset = GeneratedAsset(
            data=self.data,
            mime_type="video/mp4",
            provider=self.name,
            model=request.model,
            media_kind=MediaKind.VIDEO,
            external_id=f"fake-video-{self.calls}",
            cost_usd=self.cost_usd,
        )
        return VideoSubmission(
            id=asset.external_id or "",
            provider=self.name,
            model=request.model,
            completed=True,
            asset=asset,
        )

    def poll(self, submission: VideoSubmission) -> VideoSubmission:
        return submission
