from __future__ import annotations

import unittest
from unittest.mock import patch

from support import isolated_shortly_env

from shortly.config import Settings
from shortly.llm.base import LLMClient
from shortly.llm.factory import get_llm


class LlmFactoryContractTests(unittest.TestCase):
    def test_unsupported_provider_returns_unavailable_base_client(self) -> None:
        client = get_llm(
            Settings(
                llm_provider="none",
                text_model="fixture-text",
                vision_model="fixture-vision",
            )
        )
        self.assertIs(type(client), LLMClient)
        self.assertFalse(client.available)
        self.assertEqual(client.text_model, "fixture-text")
        self.assertEqual(client.vision_model, "fixture-vision")

    def test_generation_role_uses_generation_provider_when_configured(self) -> None:
        settings = Settings(
            llm_provider="gemini",
            text_model="analysis-text",
            vision_model="analysis-vision",
            gen_provider="anthropic",
            gen_text_model="generation-text",
        )
        sentinel = LLMClient()
        with patch("shortly.llm.factory._make_client", return_value=sentinel) as make:
            client = get_llm(settings, role="generation")
        self.assertIs(client, sentinel)
        make.assert_called_once_with(
            "anthropic",
            "generation-text",
            "generation-text",
            settings,
        )

    def test_generation_role_falls_back_to_analysis_configuration(self) -> None:
        settings = Settings(
            llm_provider="openai",
            text_model="analysis-text",
            vision_model="analysis-vision",
            gen_provider="",
            gen_text_model="",
        )
        with patch("shortly.llm.factory._make_client", return_value=LLMClient()) as make:
            get_llm(settings, role="generation")
        make.assert_called_once_with(
            "openai",
            "analysis-text",
            "analysis-vision",
            settings,
        )

    def test_factory_does_not_require_environment_when_settings_are_supplied(self) -> None:
        with isolated_shortly_env():
            client = get_llm(Settings(llm_provider="none"))
        self.assertFalse(client.available)


if __name__ == "__main__":
    unittest.main()
