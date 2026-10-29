"""Provider-neutral generation ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..domain.assets import MediaKind


class AssetGenerationError(RuntimeError):
    """Normalized generation failure safe for job-state handling."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "generation_failed",
        retryable: Optional[bool] = None,
        recommended_action: str = "",
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.recommended_action = recommended_action
        self.http_status = http_status


class ProviderUnavailableError(AssetGenerationError):
    """The configured optional provider cannot be used."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="provider_unavailable",
            retryable=False,
            recommended_action="check_settings",
        )


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    model: str = "gpt-image-2"
    size: str = "1024x1536"
    quality: str = "medium"
    output_format: str = "png"
    idempotency_key: str = ""


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    model: str = "gemini-omni-1.1-flash"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 4
    source_image: Optional[bytes] = None
    source_image_mime_type: Optional[str] = None
    idempotency_key: str = ""
    end_image: Optional[bytes] = None
    end_image_mime_type: Optional[str] = None


@dataclass(frozen=True)
class GeneratedAsset:
    data: bytes
    mime_type: str
    provider: str
    model: str
    media_kind: MediaKind
    external_id: Optional[str] = None
    cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoSubmission:
    id: str
    provider: str
    model: str
    completed: bool = False
    asset: Optional[GeneratedAsset] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ImageGenerationProvider(Protocol):
    name: str

    def generate(self, request: ImageGenerationRequest) -> GeneratedAsset:
        ...


class VideoGenerationProvider(Protocol):
    name: str

    def submit(self, request: VideoGenerationRequest) -> VideoSubmission:
        ...

    def poll(self, submission: VideoSubmission) -> VideoSubmission:
        ...
