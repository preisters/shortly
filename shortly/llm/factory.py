"""설정과 제품 레지스트리에 따라 LLM 클라이언트를 생성합니다."""

from __future__ import annotations

from typing import Optional

from ..config import (
    Settings,
    _DEFAULT_TEXT_MODELS,
    _DEFAULT_VISION_MODELS,
    get_settings,
)
from .base import LLMClient
from .registry import registry_for_product


def _make_client(
    provider: str,
    text_model: str,
    vision_model: str,
    settings: Settings,
) -> LLMClient:
    """제품이 허용한 공급자만 생성하고 나머지는 unavailable로 처리합니다."""

    registry = registry_for_product(settings.product)
    return registry.create(provider, settings, text_model, vision_model)


def get_llm(
    settings: Optional[Settings] = None,
    role: str = "analysis",
) -> LLMClient:
    """현재 설정 기반 LLM 클라이언트를 반환합니다.

    role="analysis"는 학습·분석 설정을, role="generation"은 별도 생성 설정을
    우선 사용합니다. 제품 레지스트리에 없는 공급자는 네트워크를 호출하지 않는
    unavailable 기본 클라이언트로 반환됩니다.
    """

    settings = settings or get_settings()

    if role == "generation" and settings.gen_provider:
        provider = settings.gen_provider
        text_model = (
            settings.gen_text_model
            or _DEFAULT_TEXT_MODELS.get(provider, "")
        )
        vision_model = (
            settings.gen_text_model
            or _DEFAULT_VISION_MODELS.get(provider, "")
        )
    else:
        provider = settings.llm_provider
        text_model = settings.text_model
        vision_model = settings.vision_model

    return _make_client(provider, text_model, vision_model, settings)
