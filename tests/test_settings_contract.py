from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from support import isolated_shortly_env

from shortly import config


class SettingsContractTests(unittest.TestCase):
    def test_development_defaults_remain_backward_compatible(self) -> None:
        with isolated_shortly_env():
            settings = config.Settings.load()

        self.assertFalse(settings.portable)
        self.assertTrue(settings.sfx_enabled)
        self.assertEqual(settings.llm_provider, "gemini")
        self.assertEqual(settings.data_dir, config.PROJECT_ROOT / "data")
        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8756)

    def test_portable_profile_forces_provider_models_and_user_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with isolated_shortly_env(
                SHORTLY_PORTABLE="1",
                LOCALAPPDATA=temp,
            ):
                settings = config.Settings.load()

        user_root = Path(temp) / "Shortly"
        self.assertTrue(settings.portable)
        self.assertFalse(settings.sfx_enabled)
        self.assertEqual(settings.llm_provider, "anthropic")
        self.assertEqual(settings.gen_provider, "anthropic")
        self.assertEqual(settings.text_model, config.PORTABLE_ANALYSIS_MODEL)
        self.assertEqual(settings.gen_text_model, config.PORTABLE_GENERATION_MODEL)
        self.assertEqual(settings.data_dir, user_root / "data")
        self.assertEqual(settings.tts_output_dir, user_root / "tts_output")

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


if __name__ == "__main__":
    unittest.main()
