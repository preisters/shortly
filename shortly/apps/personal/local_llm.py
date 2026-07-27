"""Personal-only OpenAI-compatible local LLM adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...llm.base import LLMClient
from ...llm.openai_transport import OpenAICompatibleClient

if TYPE_CHECKING:
    from ...config import Settings


LOCAL_PROVIDER_NAMES = ("local", "ollama", "lmstudio", "llamacpp")


def make_local_client(
    settings: "Settings",
    text_model: str,
    vision_model: str,
) -> LLMClient:
    return OpenAICompatibleClient(
        settings.local_api_key,
        text_model,
        vision_model or text_model,
        base_url=settings.local_base_url,
        provider="local",
        no_think=settings.local_no_think,
    )
