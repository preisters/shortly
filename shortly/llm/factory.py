"""설정에 따라 적절한 LLM 클라이언트를 생성합니다."""

from __future__ import annotations

from typing import Optional

from ..config import Settings, get_settings, _DEFAULT_TEXT_MODELS, _DEFAULT_VISION_MODELS
from .base import LLMClient

_LOCAL_PROVIDERS = ("local", "ollama", "lmstudio", "llamacpp")


def _make_client(provider: str, text_model: str, vision_model: str,
                 settings: Settings) -> LLMClient:
    """제공자명에 맞는 LLM 클라이언트를 생성합니다."""
    if provider == "gemini":
        from .gemini import GeminiClient
        return GeminiClient(settings.gemini_api_key, text_model, vision_model)
    if provider == "openai":
        from .openai_client import OpenAIClient
        return OpenAIClient(settings.openai_api_key, text_model, vision_model)
    if provider == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(settings.anthropic_api_key, text_model, vision_model)

    if provider in _LOCAL_PROVIDERS:
        from .openai_client import OpenAIClient
        # 멀티모달 단일 모델(예: LM Studio 의 Qwen3.6-27B)이면 비전 모델을
        # 따로 지정하지 않아도 텍스트 모델을 그대로 씁니다.
        return OpenAIClient(
            settings.local_api_key, text_model, vision_model or text_model,
            base_url=settings.local_base_url, provider="local",
            no_think=settings.local_no_think,
        )

    # provider == "none" 또는 미지원
    return LLMClient(text_model, vision_model)


def get_llm(settings: Optional[Settings] = None,
            role: str = "analysis") -> LLMClient:
    """현재 설정 기반 LLM 클라이언트. 사용 불가하면 available=False 인 기본 클라이언트 반환.

    role="analysis"  → 학습/영상 분석용(기본 llm_provider).
    role="generation" → 대본/가이드 생성용. gen_provider 가 설정돼 있으면 그것을,
                         비어 있으면 분석용 설정을 그대로 사용합니다.

    호출부는 client.available 로 사용 가능 여부를 확인하고, 불가 시 휴리스틱 대체
    로직으로 넘어가야 합니다.
    """
    settings = settings or get_settings()

    if role == "generation" and settings.gen_provider:
        provider = settings.gen_provider
        text_model = settings.gen_text_model or _DEFAULT_TEXT_MODELS.get(provider, "")
        vision_model = settings.gen_text_model or _DEFAULT_VISION_MODELS.get(provider, "")
    else:
        provider = settings.llm_provider
        text_model = settings.text_model
        vision_model = settings.vision_model

    return _make_client(provider, text_model, vision_model, settings)
