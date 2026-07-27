"""OpenAI 호환 제공자.

OpenAI 공식 API 뿐 아니라 base_url 을 지정하면 로컬 OpenAI 호환 서버
(Ollama, LM Studio, llama.cpp --server 등)에도 그대로 붙습니다.
"""

from __future__ import annotations

import base64
from pathlib import Path

from .base import LLMClient, LLMError, LLMUnavailable, strip_think


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self, api_key: str, text_model: str, vision_model: str,
                 base_url: str = "", provider: str = "openai",
                 no_think: bool = False) -> None:
        # 로컬 엔드포인트는 gpt-4o 같은 기본값을 강제하지 않습니다(사용자가 모델명 지정).
        default = "" if base_url else "gpt-4o"
        super().__init__(text_model or default, vision_model or default)
        self.provider = provider
        self._base_url = base_url
        # Qwen3 등 reasoning 모델의 thinking 을 끄고 싶을 때(속도·토큰 절약)
        self._no_think = no_think
        self._client = None
        self.available = False
        # 로컬 서버는 키가 필요 없으므로 임의 문자열을 사용합니다.
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
                    "openai 패키지 미설치. 로컬 서버 사용에도 필요합니다: pip install openai")
            raise LLMUnavailable("openai 미설치 또는 OPENAI_API_KEY 미설정")
        return self._client

    def complete_text(self, system: str, user: str, temperature: float = 0.7,
                      max_tokens: int = 4096) -> str:
        client = self._ensure()
        if self._no_think:
            system = (system or "") + " /no_think"
        try:
            resp = client.chat.completions.create(
                model=self.text_model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return strip_think(resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI 호출 실패: {exc}") from exc

    def describe_image(self, image_path: Path, prompt: str,
                       temperature: float = 0.4, max_tokens: int = 1024) -> str:
        client = self._ensure()
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        # 로컬 reasoning 모델(Qwen3 등)의 thinking 폭주로 비전 응답이 무한정
        # 길어지는 것을 막기 위해 /no_think 지시 + 토큰 상한을 둡니다.
        if self._no_think or self._base_url:
            prompt = (prompt or "") + " /no_think"
        try:
            resp = client.chat.completions.create(
                model=self.vision_model,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return strip_think(resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI 비전 호출 실패: {exc}") from exc
