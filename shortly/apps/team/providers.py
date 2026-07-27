"""LLM providers packaged for the team product."""

from __future__ import annotations

from ...llm.registry import (
    ProviderRegistration,
    ProviderRegistry,
    _anthropic,
)


def provider_registry() -> ProviderRegistry:
    # The initial team distribution fixes both roles to Anthropic.
    return ProviderRegistry((ProviderRegistration("anthropic", _anthropic),))
