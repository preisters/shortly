"""Google Gemini Omni adapter using the current REST Interactions schema.

Paid submissions use a single HTTP POST with transport retries disabled. An
injected SDK-like client remains available for existing callers and test doubles;
its retry policy belongs to that caller, not to this adapter.
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


_OMNI_OUTPUT_DURATION_SECONDS = 4
_OMNI_OUTPUT_RESOLUTION = "720p"
_INTERACTIONS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _value(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def google_error_details(exc: Exception, api_key: str = "") -> dict[str, Any]:
    """Keep provider diagnostics without retaining credentials/request pixels."""
    response = getattr(exc, "response", None)
    body = getattr(exc, "body", None)
    if body is None and response is not None:
        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = None
    provider_error = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(provider_error, dict):
        provider_error = {"message": str(provider_error)}
    headers = getattr(response, "headers", {}) or {}

    def clean(value: Any) -> str:
        text = str(value or "")
        return text.replace(api_key, "[REDACTED]")[:4000] if api_key else text[:4000]

    return {
        "exception_type": type(exc).__name__,
        "http_status": getattr(response, "status_code", None)
        or getattr(exc, "status_code", None),
        "provider_code": clean(provider_error.get("code")),
        "provider_status": clean(provider_error.get("status")),
        "provider_message": clean(provider_error.get("message")),
        "provider_details": clean(provider_error.get("details")),
        "request_id": clean(headers.get("x-request-id") or headers.get("x-goog-request-id")),
    }


def _google_failure(exc: Exception, api_key: str = "") -> AssetGenerationError:
    """Preserve HTTP/SDK diagnostics; retryable does not authorize a paid retry."""
    diagnostic = google_error_details(exc, api_key)
    raw_status = (
        diagnostic["http_status"]
        or getattr(exc, "code", None)
    )
    try:
        status = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        status = None
    detail = diagnostic["provider_message"] or str(exc).strip()
    if api_key:
        detail = detail.replace(api_key, "[REDACTED]")
    detail = detail[:600]
    lowered = detail.lower()
    if status in (401, 403) or any(
        token in lowered for token in ("api key", "permission denied", "unauthenticated")
    ):
        code, retryable, action = "authentication_failed", False, "check_settings"
    elif status == 429 and any(
        token in lowered for token in ("quota", "billing", "credit", "resource_exhausted")
    ):
        code, retryable, action = "quota_exceeded", False, "check_billing"
    elif status == 429:
        code, retryable, action = "rate_limited", True, "retry_later"
    elif status is not None and status >= 500:
        code, retryable, action = "provider_server_error", True, "retry_later"
    elif diagnostic["provider_code"] == "content_blocked" or any(
        token in lowered
        for token in ("safety", "policy", "blocked", "prohibited", "content filter")
    ):
        code, retryable, action = "content_policy_rejected", False, "revise_prompt"
    elif any(token in lowered for token in ("timeout", "timed out", "connection")):
        code, retryable, action = "network_error", True, "retry_later"
    elif status == 400:
        code, retryable, action = "invalid_request", False, "revise_prompt"
    else:
        code, retryable, action = "video_submission_failed", None, "inspect_error"
    technical = f"{type(exc).__name__}"
    if status is not None:
        technical += f" / HTTP {status}"
    if diagnostic["provider_code"]:
        technical += f" / {diagnostic['provider_code']}"
    if detail:
        technical += f": {detail}"
    return AssetGenerationError(
        f"Google video submission failed: {technical}",
        code=code,
        retryable=retryable,
        recommended_action=action,
        http_status=status,
    )


class GoogleVideoProvider:
    name = "google"

    def __init__(self, api_key: str, *, client: Optional[Any] = None,
                 transport: Optional[Any] = None):
        self._api_key = api_key.strip()
        self._client = client
        self._transport = transport

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise ProviderUnavailableError("Google API key is not configured.")
        return None

    def _create_rest(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            raise ProviderUnavailableError("httpx is required for Google video generation.") from exc
        transport = self._transport if self._transport is not None else httpx.HTTPTransport(retries=0)
        with httpx.Client(transport=transport, timeout=240.0, follow_redirects=False) as client:
            response = client.post(
                _INTERACTIONS_ENDPOINT,
                headers={"x-goog-api-key": self._api_key},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _asset(
        response: Any,
        model: str,
        duration_seconds: int,
    ) -> Optional[GeneratedAsset]:
        video = _value(response, "output_video")
        if video is None:
            for step in _value(response, "steps", []) or []:
                if _value(step, "type") != "model_output":
                    continue
                video = next((item for item in _value(step, "content", []) or []
                              if _value(item, "type") == "video"), None)
                if video is not None:
                    break
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
            metadata={
                "status": _value(response, "status"),
                "output_duration_sec": duration_seconds,
                "requested_resolution": _OMNI_OUTPUT_RESOLUTION,
            },
        )

    def submit(self, request: VideoGenerationRequest) -> VideoSubmission:
        if request.end_image is not None and request.source_image is None:
            raise AssetGenerationError("An end image requires a start image.",
                                       code="invalid_request", retryable=False)
        client = self._get_client()
        interactions = getattr(client, "interactions", None) if client is not None else None
        if client is not None and (interactions is None or not hasattr(interactions, "create")):
            raise ProviderUnavailableError(
                "Installed Google Gen AI SDK does not provide the Interactions API."
            )
        input_value: Any = request.prompt
        if request.source_image is not None:
            input_value = [
                {
                    "type": "image",
                    "data": base64.b64encode(request.source_image).decode("ascii"),
                    "mime_type": request.source_image_mime_type or "image/png",
                },
            ]
            if request.end_image is not None:
                input_value.append({
                    "type": "image",
                    "data": base64.b64encode(request.end_image).decode("ascii"),
                    "mime_type": request.end_image_mime_type or "image/png",
                })
            input_value.append({"type": "text", "text": request.prompt})
        payload = {
            "model": request.model,
            "input": input_value,
            "response_format": {
                "type": "video",
                "aspect_ratio": request.aspect_ratio,
                "duration": f"{_OMNI_OUTPUT_DURATION_SECONDS}s",
                "resolution": _OMNI_OUTPUT_RESOLUTION,
                "delivery": "inline",
            },
        }
        try:
            response = interactions.create(**payload) if interactions is not None else self._create_rest(payload)
        except Exception as exc:
            raise _google_failure(exc, self._api_key) from exc
        asset = self._asset(
            response,
            request.model,
            _OMNI_OUTPUT_DURATION_SECONDS,
        )
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
