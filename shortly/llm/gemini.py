"""Google Gemini 제공자."""

from __future__ import annotations

from pathlib import Path

from .base import LLMClient, LLMUnavailable, normalize_provider_error


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, api_key: str, text_model: str, vision_model: str) -> None:
        super().__init__(text_model or "gemini-1.5-pro", vision_model or "gemini-1.5-pro")
        self._api_key = api_key
        self._genai = None
        self.available = False
        if not api_key:
            return
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError:
            return
        genai.configure(api_key=api_key)
        self._genai = genai
        self.available = True

    def _model(self, name: str):
        if self._genai is None:
            raise LLMUnavailable("google-generativeai 미설치 또는 GEMINI_API_KEY 미설정")
        return self._genai.GenerativeModel(name)

    def complete_text(self, system: str, user: str, temperature: float = 0.7,
                      max_tokens: int = 4096) -> str:
        model = self._model(self.text_model)
        try:
            resp = model.generate_content(
                [{"role": "user", "parts": [system + "\n\n" + user]}],
                generation_config={"temperature": temperature,
                                   "max_output_tokens": max_tokens},
            )
            return (resp.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise normalize_provider_error("gemini", exc) from exc

    def describe_image(self, image_path: Path, prompt: str,
                       temperature: float = 0.4, max_tokens: int = 1024) -> str:
        model = self._model(self.vision_model)
        data = Path(image_path).read_bytes()
        try:
            resp = model.generate_content(
                [prompt, {"mime_type": "image/jpeg", "data": data}],
                generation_config={"temperature": temperature,
                                   "max_output_tokens": max_tokens},
            )
            return (resp.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise normalize_provider_error(
                "gemini", exc, operation="비전 호출"
            ) from exc
