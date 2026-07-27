"""LLM providers available to the personal and legacy products."""

from __future__ import annotations

from ...llm.registry import (
    ProviderRegistration,
    ProviderRegistry,
    _anthropic,
    _gemini,
    _openai,
)
from .local_llm import LOCAL_PROVIDER_NAMES, make_local_client


def provider_registry() -> ProviderRegistry:
    registrations = [
        ProviderRegistration("gemini", _gemini),
        ProviderRegistration("openai", _openai),
        ProviderRegistration("anthropic", _anthropic),
    ]
    registrations.extend(
        ProviderRegistration(name, make_local_client, local=True)
        for name in LOCAL_PROVIDER_NAMES
    )
    return ProviderRegistry(registrations)
