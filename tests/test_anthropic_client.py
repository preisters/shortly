from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import ROOT  # noqa: F401

from shortly.llm.anthropic_client import AnthropicClient


class _Messages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")]
        )


class _Client:
    def __init__(self) -> None:
        self.messages = _Messages()


def _client(model: str) -> tuple[AnthropicClient, _Client]:
    adapter = AnthropicClient("", model, model)
    fake = _Client()
    adapter._client = fake
    adapter.available = True
    return adapter, fake


class AnthropicClientTests(unittest.TestCase):
    def test_opus_5_omits_rejected_temperature_parameter(self) -> None:
        adapter, fake = _client("claude-opus-5")

        result = adapter.complete_text(
            "system",
            "user",
            temperature=0.85,
            max_tokens=7000,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(len(fake.messages.calls), 1)
        self.assertNotIn("temperature", fake.messages.calls[0])
        self.assertEqual(fake.messages.calls[0]["model"], "claude-opus-5")
        self.assertEqual(fake.messages.calls[0]["max_tokens"], 7000)

    def test_legacy_model_keeps_temperature_parameter(self) -> None:
        adapter, fake = _client("claude-sonnet-4-6")

        adapter.complete_text("system", "user", temperature=0.4)

        self.assertEqual(fake.messages.calls[0]["temperature"], 0.4)

    def test_opus_5_vision_request_also_omits_temperature(self) -> None:
        adapter, fake = _client("claude-opus-5")
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "fixture.jpg"
            image.write_bytes(b"fixture")
            result = adapter.describe_image(
                image,
                "describe",
                temperature=0.4,
            )

        self.assertEqual(result, "ok")
        self.assertNotIn("temperature", fake.messages.calls[0])


if __name__ == "__main__":
    unittest.main()
