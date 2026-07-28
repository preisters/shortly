"""Google Gemini Omni video adapter.

The SDK is imported lazily so it stays outside the team artifact and is only a
runtime requirement when this personal provider is selected.
"""

from __future__ import annotations

import base64
from typing import Any, Optional

from ....core.domain.assets import MediaKind
from ....core.ports.assets import (
    AssetGenerationError,
    GeneratedAsset,
    ProviderUnavailableError,
    VideoGenerationRequest,
    VideoSubmission,
)


def _value(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class GoogleVideoProvider:
    name = "google"

    def __init__(self, api_key: str, *, client: Optional[Any] = None):
        self._api_key = api_key.strip()
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise ProviderUnavailableError("Google API key is not configured.")
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderUnavailableError(
                "Google Gen AI SDK is required for video generation."
            ) from exc
        self._client = genai.Client(api_key=self._api_key)
        return self._client

    @staticmethod
    def _asset(response: Any, model: str) -> Optional[GeneratedAsset]:
        video = _value(response, "output_video")
        if video is None:
            outputs = _value(response, "outputs", []) or []
            video = outputs[0] if outputs else None
        encoded = _value(video, "data") if video is not None else None
        if not encoded:
            return None
        if isinstance(encoded, str):
            data = base64.b64decode(encoded)
        else:
            data = bytes(encoded)
        return GeneratedAsset(
            data=data,
            mime_type=_value(video, "mime_type", "video/mp4"),
            provider="google",
            model=model,
            media_kind=MediaKind.VIDEO,
            external_id=_value(response, "id"),
            metadata={"status": _value(response, "status")},
        )

    def submit(self, request: VideoGenerationRequest) -> VideoSubmission:
        client = self._get_client()
        input_value: Any = request.prompt
        if request.source_image is not None:
            input_value = [
                {"type": "text", "text": request.prompt},
                {
                    "type": "image",
                    "data": base64.b64encode(request.source_image).decode("ascii"),
                    "mime_type": request.source_image_mime_type or "image/png",
                },
            ]
        try:
            response = client.interactions.create(
                model=request.model,
                input=input_value,
                response_format={
                    "type": "video",
                    "aspect_ratio": request.aspect_ratio,
                },
            )
        except Exception as exc:
            raise AssetGenerationError(
                f"Google video submission failed: {type(exc).__name__}"
            ) from exc
        asset = self._asset(response, request.model)
        submission_id = str(_value(response, "id") or "")
        if asset is None and not submission_id:
            raise AssetGenerationError(
                "Google video response had neither video data nor a job id."
            )
        return VideoSubmission(
            id=submission_id,
            provider=self.name,
            model=request.model,
            completed=asset is not None,
            asset=asset,
            metadata={"status": _value(response, "status")},
        )

    def poll(self, submission: VideoSubmission) -> VideoSubmission:
        if submission.completed:
            return submission
        raise ProviderUnavailableError(
            "This Gemini Omni adapter expects inline video delivery. "
            "URI-delivery polling is not enabled."
        )
