from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from support import isolated_shortly_env

from shortly import config
from shortly.product_profile import ProductKind


class SettingsContractTests(unittest.TestCase):
    def test_development_defaults_remain_backward_compatible(self) -> None:
        with isolated_shortly_env():
            settings = config.Settings.load()

        self.assertFalse(settings.portable)
        self.assertTrue(settings.sfx_enabled)
        self.assertIs(settings.product, ProductKind.LEGACY)
        self.assertTrue(settings.features.local_llm)
        self.assertEqual(settings.llm_provider, "gemini")
        self.assertEqual(settings.data_dir, config.PROJECT_ROOT / "data")
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8756)
        self.assertTrue(settings.local_auto_unload)

    def test_portable_profile_forces_provider_models_and_user_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = Path(temp) / "documents" / "Shortly"
            with isolated_shortly_env(
                SHORTLY_PORTABLE="1",
                LOCALAPPDATA=temp,
                SHORTLY_ARTIFACT_DIR=str(artifacts),
            ):
                settings = config.Settings.load()

        user_root = Path(temp) / "Shortly"
        self.assertTrue(settings.portable)
        self.assertFalse(settings.sfx_enabled)
        self.assertIs(settings.product, ProductKind.TEAM)
        self.assertFalse(settings.features.local_llm)
        self.assertEqual(settings.llm_provider, "anthropic")
        self.assertEqual(settings.gen_provider, "anthropic")
        self.assertEqual(settings.text_model, config.PORTABLE_ANALYSIS_MODEL)
        self.assertEqual(settings.gen_text_model, config.PORTABLE_GENERATION_MODEL)
        self.assertEqual(settings.gen_text_model, "claude-opus-5")
        self.assertEqual(settings.data_dir, user_root / "data")
        self.assertEqual(settings.artifact_dir, artifacts)
        self.assertEqual(settings.log_dir, artifacts / "logs")
        self.assertEqual(settings.tts_output_dir, artifacts / "tts")

    def test_personal_profile_has_separate_roots_without_changing_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = Path(temp) / "documents" / "Shortly Personal"
            with isolated_shortly_env(
                SHORTLY_PRODUCT="personal",
                LOCALAPPDATA=temp,
                SHORTLY_ARTIFACT_DIR=str(artifacts),
                SHORTLY_LLM_PROVIDER="openai",
                SHORTLY_TEXT_MODEL="fixture-model",
            ):
                settings = config.Settings.load()

        self.assertIs(settings.product, ProductKind.PERSONAL)
        self.assertFalse(settings.portable)
        self.assertTrue(settings.features.local_llm)
        self.assertTrue(settings.features.personal_automation)
        self.assertEqual(settings.llm_provider, "openai")
        self.assertEqual(settings.text_model, "fixture-model")
        self.assertEqual(
            settings.data_dir,
            Path(temp) / "ShortlyPersonal" / "data",
        )
        self.assertEqual(settings.artifact_dir, artifacts)
        self.assertEqual(settings.tts_output_dir, artifacts / "tts")

    def test_dotenv_override_order_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.env"
            second = Path(temp) / "second.env"
            first.write_text("SHORTLY_TEST_PRECEDENCE=first\n", encoding="utf-8")
            second.write_text("SHORTLY_TEST_PRECEDENCE=second\n", encoding="utf-8")

            old = os.environ.pop("SHORTLY_TEST_PRECEDENCE", None)
            try:
                config._load_dotenv(first, override=False)
                config._load_dotenv(second, override=False)
                self.assertEqual(os.environ["SHORTLY_TEST_PRECEDENCE"], "first")
                config._load_dotenv(second, override=True)
                self.assertEqual(os.environ["SHORTLY_TEST_PRECEDENCE"], "second")
            finally:
                os.environ.pop("SHORTLY_TEST_PRECEDENCE", None)
                if old is not None:
                    os.environ["SHORTLY_TEST_PRECEDENCE"] = old

    def test_key_lookup_supports_commercial_and_local_providers(self) -> None:
        settings = config.Settings(
            gemini_api_key="g",
            openai_api_key="o",
            anthropic_api_key="a",
            local_api_key="local",
        )
        self.assertEqual(settings.key_for("gemini"), "g")
        self.assertEqual(settings.key_for("openai"), "o")
        self.assertEqual(settings.key_for("anthropic"), "a")
        for provider in ("local", "ollama", "lmstudio", "llamacpp"):
            self.assertEqual(settings.key_for(provider), "local")
        self.assertEqual(settings.key_for("unsupported"), "")

    def test_product_identity_cannot_be_changed_by_settings_ui(self) -> None:
        self.assertNotIn("SHORTLY_PRODUCT", config.SAVABLE_KEYS)


if __name__ == "__main__":
    unittest.main()
