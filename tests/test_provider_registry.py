from __future__ import annotations

import unittest

from shortly.apps.personal.local_llm import LOCAL_PROVIDER_NAMES
from shortly.apps.personal.providers import (
    provider_registry as personal_provider_registry,
)
from shortly.apps.team.providers import provider_registry as team_provider_registry
from shortly.config import Settings
from shortly.llm.base import LLMClient
from shortly.llm.factory import get_llm
from shortly.llm.openai_transport import OpenAICompatibleClient
from shortly.product_profile import ProductKind


class ProviderRegistryTests(unittest.TestCase):
    def test_team_registry_contains_only_packaged_provider(self) -> None:
        self.assertEqual(team_provider_registry().names, ("anthropic",))

    def test_personal_registry_preserves_commercial_and_local_aliases(self) -> None:
        names = personal_provider_registry().names
        for provider in ("gemini", "openai", "anthropic", *LOCAL_PROVIDER_NAMES):
            self.assertIn(provider, names)

    def test_team_rejects_local_provider_without_loading_adapter(self) -> None:
        client = get_llm(
            Settings(
                product=ProductKind.TEAM,
                llm_provider="local",
                text_model="fixture-local",
                vision_model="fixture-vision",
            )
        )
        self.assertIs(type(client), LLMClient)
        self.assertFalse(client.available)

    def test_personal_local_provider_uses_personal_adapter(self) -> None:
        client = get_llm(
            Settings(
                product=ProductKind.PERSONAL,
                llm_provider="local",
                text_model="fixture-local",
                vision_model="",
                local_api_key="",
            )
        )
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual(client.provider, "local")
        self.assertEqual(client.text_model, "fixture-local")
        self.assertEqual(client.vision_model, "fixture-local")


if __name__ == "__main__":
    unittest.main()
