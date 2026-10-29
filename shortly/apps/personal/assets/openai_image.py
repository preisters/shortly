"""OpenAI Images adapter for GPT Image 2."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from ....core.domain.assets import MediaKind
from ....core.ports.assets import (
    AssetGenerationError,
    GeneratedAsset,
    ImageGenerationRequest,
    ProviderUnavailableError,
)


class OpenAIImageProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://api.openai.com/v1/images/generations",
        timeout: float = 180.0,
        transport: Optional[
            Callable[[urllib.request.Request, float], dict[str, Any]]
        ] = None,
    ):
        self._api_key = api_key.strip()
        self._endpoint = endpoint
        self._timeout = timeout
        self._transport = transport or self._send

    @staticmethod
    def _send(
        request: urllib.request.Request, timeout: float
    ) -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def generate(self, request: ImageGenerationRequest) -> GeneratedAsset:
        if not self._api_key:
            raise ProviderUnavailableError("OpenAI API key is not configured.")
        body = {
            "model": request.model,
            "prompt": request.prompt,
            "size": request.size,
            "quality": request.quality,
            "output_format": request.output_format,
            "n": 1,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if request.idempotency_key:
            headers["Idempotency-Key"] = request.idempotency_key
        http_request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            payload = self._transport(http_request, self._timeout)
            item = (payload.get("data") or [])[0]
            encoded = item.get("b64_json")
            if not encoded:
                raise AssetGenerationError(
                    "OpenAI image response did not contain image data."
                )
            data = base64.b64decode(encoded, validate=True)
        except AssetGenerationError:
            raise
        except urllib.error.HTTPError as exc:
            detail = ""
            provider_code = ""
            try:
                error_payload = json.loads(exc.read().decode("utf-8", errors="replace"))
                error_value = error_payload.get("error") or {}
                detail = str(error_value.get("message") or "").strip()
                provider_code = str(
                    error_value.get("code") or error_value.get("type") or ""
                ).strip()
            except (ValueError, AttributeError, OSError):
                detail = str(getattr(exc, "reason", "") or "").strip()
            status = int(exc.code)
            lowered = f"{provider_code} {detail}".lower()
            if status in (401, 403):
                code, retryable, action = "authentication_failed", False, "check_settings"
            elif status == 429 and any(
                token in lowered
                for token in ("quota", "billing", "credit", "insufficient")
            ):
                code, retryable, action = "quota_exceeded", False, "check_billing"
            elif status == 429:
                code, retryable, action = "rate_limited", True, "retry_later"
            elif status >= 500:
                code, retryable, action = "provider_server_error", True, "retry_later"
            else:
                code, retryable, action = "invalid_request", False, "revise_prompt"
            technical = f"HTTP {status}"
            if provider_code:
                technical += f" / {provider_code}"
            if detail:
                technical += f": {detail}"
            raise AssetGenerationError(
                f"OpenAI image generation failed: {technical}",
                code=code,
                retryable=retryable,
                recommended_action=action,
                http_status=status,
            ) from exc
        except urllib.error.URLError as exc:
            raise AssetGenerationError(
                f"OpenAI image network request failed: {exc.reason}",
                code="network_error",
                retryable=True,
                recommended_action="retry_later",
            ) from exc
        except (ValueError, KeyError, IndexError) as exc:
            raise AssetGenerationError(
                f"OpenAI image response could not be decoded: {type(exc).__name__}",
                code="invalid_provider_response",
                retryable=True,
                recommended_action="retry_later",
            ) from exc
        mime = {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }.get(request.output_format, "application/octet-stream")
        return GeneratedAsset(
            data=data,
            mime_type=mime,
            provider=self.name,
            model=request.model,
            media_kind=MediaKind.IMAGE,
            external_id=payload.get("id"),
            metadata={
                "revised_prompt": item.get("revised_prompt"),
                "usage": payload.get("usage") or {},
            },
        )
