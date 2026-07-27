"""Anthropic Claude 제공자."""

from __future__ import annotations

import base64
from pathlib import Path

from .base import LLMClient, LLMError, LLMUnavailable


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, api_key: str, text_model: str, vision_model: str) -> None:
        model = text_model or "claude-3-5-sonnet-latest"
        super().__init__(model, vision_model or model)
        self._client = None
        self.available = False
        if not api_key:
            return
        try:
            import anthropic  # type: ignore
        except ImportError:
            return
        self._client = anthropic.Anthropic(api_key=api_key)
        self.available = True

    def _ensure(self):
        if self._client is None:
            raise LLMUnavailable("anthropic 미설치 또는 ANTHROPIC_API_KEY 미설정")
        return self._client

    @staticmethod
    def _is_temperature_unsupported(exc: Exception) -> bool:
        # Opus 4.7+ / Sonnet 5 등은 temperature 등 샘플링 파라미터를 거부(400).
        msg = str(exc).lower()
        return "temperature" in msg and ("deprecat" in msg or "not support" in msg
                                         or "unsupported" in msg)

    def _create(self, **kwargs):
        """temperature 미지원 모델이면 해당 파라미터를 빼고 자동 재시도."""
        client = self._ensure()
        try:
            return client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if "temperature" in kwargs and self._is_temperature_unsupported(exc):
                kwargs.pop("temperature", None)
                try:
                    return client.messages.create(**kwargs)
                except Exception as exc2:  # noqa: BLE001
                    raise LLMError(f"Anthropic 호출 실패: {exc2}") from exc2
            raise LLMError(f"Anthropic 호출 실패: {exc}") from exc

    def complete_text(self, system: str, user: str, temperature: float = 0.7,
                      max_tokens: int = 4096) -> str:
        resp = self._create(
            model=self.text_model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()

    def describe_image(self, image_path: Path, prompt: str,
                       temperature: float = 0.4, max_tokens: int = 1024) -> str:
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        resp = self._create(
            model=self.vision_model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
