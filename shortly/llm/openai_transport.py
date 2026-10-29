"""Shared OpenAI-compatible transport used by commercial and local adapters."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .base import (
    LLMClient,
    LLMError,
    LLMUnavailable,
    normalize_provider_error,
    parse_json_loose,
    strip_think,
)


class OpenAICompatibleClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        text_model: str,
        vision_model: str,
        *,
        base_url: str = "",
        provider: str = "openai",
        no_think: bool = False,
    ) -> None:
        default = "" if base_url else "gpt-4o"
        super().__init__(text_model or default, vision_model or default)
        self.provider = provider
        self._base_url = base_url
        self._no_think = no_think
        self._client = None
        self.available = False
        if base_url and not api_key:
            api_key = "local"
        if not api_key:
            return
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.available = True

    def _ensure(self):
        if self._client is None:
            if self._base_url:
                raise LLMUnavailable(
                    "openai 패키지 미설치. 로컬 서버 사용에도 필요합니다: "
                    "pip install openai"
                )
            raise LLMUnavailable("openai 미설치 또는 OPENAI_API_KEY 미설정")
        return self._client

    def complete_text(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        client = self._ensure()
        if self._no_think:
            system = (system or "") + " /no_think"
        try:
            response = client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            message = response.choices[0].message
            raw = message.content or getattr(message, "reasoning_content", "") or ""
            return strip_think(raw).strip()
        except Exception as exc:  # noqa: BLE001
            if not self._base_url:
                raise normalize_provider_error("openai", exc) from exc
            raise LLMError(f"OpenAI-compatible 호출 실패: {exc}") from exc

    def complete_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> Any:
        if not self._base_url:
            return super().complete_json(
                system,
                user,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        client = self._ensure()
        if self._no_think:
            system = (system or "") + " /no_think"
        try:
            response = client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response",
                        "strict": False,
                        "schema": {},
                    },
                },
            )
            message = response.choices[0].message
            raw = message.content or getattr(message, "reasoning_content", "") or ""
            raw = strip_think(raw).strip()
            return parse_json_loose(raw)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI-compatible JSON request failed: {exc}") from exc

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> str:
        client = self._ensure()
        encoded = base64.b64encode(Path(image_path).read_bytes()).decode()
        if self._no_think or self._base_url:
            prompt = (prompt or "") + " /no_think"
        try:
            response = client.chat.completions.create(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{encoded}"
                                },
                            },
                        ],
                    }
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            message = response.choices[0].message
            raw = message.content or getattr(message, "reasoning_content", "") or ""
            return strip_think(raw).strip()
        except Exception as exc:  # noqa: BLE001
            if not self._base_url:
                raise normalize_provider_error(
                    "openai", exc, operation="비전 호출"
                ) from exc
            raise LLMError(f"OpenAI-compatible 비전 호출 실패: {exc}") from exc
