"""LLM 클라이언트 공통 인터페이스."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional


class LLMError(RuntimeError):
    """LLM 호출 중 발생한 일반 오류."""


class LLMUnavailable(LLMError):
    """API 키 미설정 또는 SDK 미설치로 사용 불가."""


class LLMResponseParseError(LLMError):
    """The provider responded, but its text was not valid structured output."""


class LLMQuotaExceeded(LLMError):
    """상용 AI의 크레딧·결제 한도·사용 할당량이 부족한 오류."""

    error_code = "commercial_ai_quota_exceeded"
    retryable = False

    def __init__(self, provider: str) -> None:
        labels = {
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "gemini": "Gemini",
            "google": "Google",
        }
        self.provider = provider
        label = labels.get(provider.lower(), provider or "상용 AI")
        super().__init__(
            f"{label} AI 토큰·크레딧 또는 사용 할당량이 부족합니다. "
            "공급자 대시보드에서 잔액과 사용 한도를 확인한 뒤 다시 시도하세요."
        )


def _provider_error_text(exc: Exception) -> str:
    """SDK마다 다른 오류 객체에서 분류에 필요한 공개 메타데이터만 모은다."""
    values = [str(exc)]
    for name in ("code", "type", "status", "status_code", "message", "body"):
        value = getattr(exc, name, None)
        if value not in (None, ""):
            try:
                values.append(json.dumps(value, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                values.append(str(value))
    return " ".join(values).lower()[:6000]


def is_provider_quota_exceeded(exc: Exception) -> bool:
    """결제/할당량 부족을 일시적인 요청 속도 제한과 구분한다."""
    text = _provider_error_text(exc)
    markers = (
        "insufficient_quota",
        "insufficient quota",
        "insufficient_credit",
        "insufficient credit",
        "credit balance is too low",
        "purchase credits",
        "billing_hard_limit",
        "billing hard limit",
        "billing limit",
        "payment required",
        "exceeded your current quota",
        "quota exceeded",
        "quota_exceeded",
        "resource_exhausted",
    )
    return any(marker in text for marker in markers)


def normalize_provider_error(
    provider: str,
    exc: Exception,
    *,
    operation: str = "호출",
) -> LLMError:
    """공급자 예외를 UI까지 안전하게 전달할 공통 LLM 오류로 변환한다."""
    if is_provider_quota_exceeded(exc):
        return LLMQuotaExceeded(provider)
    labels = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "gemini": "Gemini",
        "google": "Google",
    }
    label = labels.get(provider.lower(), provider or "AI")
    return LLMError(f"{label} {operation} 실패: {exc}")


class LLMClient:
    """모든 제공자가 구현하는 공통 인터페이스.

    - complete_text: 시스템/유저 프롬프트 -> 텍스트
    - complete_json: 텍스트 응답을 JSON 으로 파싱(코드펜스 제거 포함)
    - describe_image: 이미지 + 프롬프트 -> 텍스트(비전)
    """

    provider: str = "base"
    available: bool = False

    def __init__(self, text_model: str = "", vision_model: str = "") -> None:
        self.text_model = text_model
        self.vision_model = vision_model

    def complete_text(self, system: str, user: str, temperature: float = 0.7,
                      max_tokens: int = 4096) -> str:
        raise LLMUnavailable("LLM 제공자가 설정되지 않았습니다.")

    def describe_image(self, image_path: Path, prompt: str,
                       temperature: float = 0.4, max_tokens: int = 1024) -> str:
        raise LLMUnavailable("비전 제공자가 설정되지 않았습니다.")

    def complete_json(self, system: str, user: str, temperature: float = 0.4,
                      max_tokens: int = 4096) -> Any:
        raw = self.complete_text(
            system + "\n\n반드시 유효한 JSON 하나만 출력하세요. 설명·코드펜스 금지.",
            user, temperature=temperature, max_tokens=max_tokens,
        )
        return parse_json_loose(raw)


def strip_think(text: str) -> str:
    """Qwen3 등 reasoning 모델의 <think>...</think> 블록을 제거합니다."""
    if not text:
        return text
    # 닫힌 블록 제거
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # 열리기만 하고 안 닫힌 경우, 마지막 </think> 이후만 취함
    if "</think>" in text.lower():
        idx = text.lower().rfind("</think>")
        text = text[idx + len("</think>"):]
    return text.strip()


def parse_json_loose(text: str) -> Any:
    """모델 응답에서 JSON 을 최대한 관대하게 추출합니다."""
    if text is None:
        raise LLMError("빈 응답")
    s = strip_think(text).strip()
    # 코드펜스 제거
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 가장 바깥 중괄호/대괄호 블록 시도
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        end = s.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMResponseParseError(f"JSON 파싱 실패: {text[:200]}")
