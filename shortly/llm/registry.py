"""Product-aware LLM provider registration.

Provider modules are imported only by their registered factory. This keeps the
team artifact independent from personal/local adapters that are not packaged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, TYPE_CHECKING

from ..product_profile import ProductKind
from .base import LLMClient

if TYPE_CHECKING:
    from ..config import Settings


ProviderFactory = Callable[["Settings", str, str], LLMClient]


@dataclass(frozen=True)
class ProviderRegistration:
    name: str
    factory: ProviderFactory
    local: bool = False


class ProviderRegistry:
    """Provider allowlist used by one product composition."""

    def __init__(self, registrations: Iterable[ProviderRegistration] = ()) -> None:
        self._registrations: Dict[str, ProviderRegistration] = {
            item.name: item for item in registrations
        }

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def create(
        self,
        provider: str,
        settings: "Settings",
        text_model: str,
        vision_model: str,
    ) -> LLMClient:
        registration = self._registrations.get((provider or "").lower())
        if registration is None:
            return LLMClient(text_model, vision_model)
        return registration.factory(settings, text_model, vision_model)


def _gemini(
    settings: "Settings",
    text_model: str,
    vision_model: str,
) -> LLMClient:
    from .gemini import GeminiClient

    return GeminiClient(settings.gemini_api_key, text_model, vision_model)


def _openai(
    settings: "Settings",
    text_model: str,
    vision_model: str,
) -> LLMClient:
    from .openai_client import OpenAIClient

    return OpenAIClient(settings.openai_api_key, text_model, vision_model)


def _anthropic(
    settings: "Settings",
    text_model: str,
    vision_model: str,
) -> LLMClient:
    from .anthropic_client import AnthropicClient

    return AnthropicClient(settings.anthropic_api_key, text_model, vision_model)


def registry_for_product(product: ProductKind) -> ProviderRegistry:
    """Load only the registry owned by the selected product composition."""

    product = ProductKind(product)
    if product is ProductKind.TEAM:
        from ..apps.team.providers import provider_registry

        return provider_registry()
    from ..apps.personal.providers import provider_registry

    # Legacy keeps the pre-split provider behavior.
    return provider_registry()
