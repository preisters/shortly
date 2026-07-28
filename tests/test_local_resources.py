from __future__ import annotations

from types import SimpleNamespace
import unittest

from shortly.apps.personal.local_llm import AutoUnloadingLocalClient
from shortly.apps.personal.local_resources import LocalModelResourceManager


class _Transport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None, dict[str, str]]] = []

    def __call__(self, method, url, payload, headers):
        self.calls.append((method, url, payload, headers))
        return self.responses.pop(0) if self.responses else {}


class _Completions:
    def create(self, **kwargs):
        message = SimpleNamespace(
            content='{"passed": true}',
            reasoning_content="",
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _ResourceManager:
    origin = "http://localhost:1234"

    def __init__(self) -> None:
        self.models: list[str] = []

    def unload(self, model: str) -> bool:
        self.models.append(model)
        return True


class LocalResourceTests(unittest.TestCase):
    def test_ollama_unload_uses_zero_keep_alive(self) -> None:
        transport = _Transport([{"done": True, "done_reason": "unload"}])
        manager = LocalModelResourceManager(
            "http://localhost:11434/v1",
            runtime="ollama",
            transport=transport,
        )

        released = manager.unload("qwen3.6:27b")

        self.assertTrue(released)
        method, url, payload, _headers = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "http://localhost:11434/api/generate")
        self.assertEqual(payload["model"], "qwen3.6:27b")
        self.assertEqual(payload["prompt"], "")
        self.assertEqual(payload["keep_alive"], 0)

    def test_lmstudio_unloads_only_matching_loaded_instances(self) -> None:
        transport = _Transport(
            [
                {
                    "models": [
                        {
                            "key": "qwen/qwen3.6-27b",
                            "loaded_instances": [
                                {"id": "qwen/qwen3.6-27b"},
                            ],
                        },
                        {
                            "key": "another/model",
                            "loaded_instances": [
                                {"id": "another/model"},
                            ],
                        },
                    ]
                },
                {"instance_id": "qwen/qwen3.6-27b"},
            ]
        )
        manager = LocalModelResourceManager(
            "http://localhost:1234/v1",
            "token",
            runtime="lmstudio",
            transport=transport,
        )

        released = manager.unload("qwen/qwen3.6-27b")

        self.assertTrue(released)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(
            transport.calls[1][2],
            {"instance_id": "qwen/qwen3.6-27b"},
        )
        self.assertEqual(
            transport.calls[1][3]["Authorization"],
            "Bearer token",
        )

    def test_successful_local_request_releases_model(self) -> None:
        client = AutoUnloadingLocalClient.__new__(
            AutoUnloadingLocalClient
        )
        client.text_model = "fixture-model"
        client.vision_model = "fixture-model"
        client._base_url = "http://localhost:1234/v1"
        client._no_think = True
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions())
        )
        client._auto_unload = True
        manager = _ResourceManager()
        client._resource_manager = manager

        value = client.complete_json("system", "user")

        self.assertEqual(value, {"passed": True})
        self.assertEqual(manager.models, ["fixture-model"])

    def test_failed_local_request_still_releases_model(self) -> None:
        class _Failure:
            def create(self, **kwargs):
                raise RuntimeError("fixture")

        client = AutoUnloadingLocalClient.__new__(
            AutoUnloadingLocalClient
        )
        client.text_model = "fixture-model"
        client.vision_model = "fixture-model"
        client._base_url = "http://localhost:1234/v1"
        client._no_think = True
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Failure())
        )
        client._auto_unload = True
        manager = _ResourceManager()
        client._resource_manager = manager

        with self.assertRaises(Exception):
            client.complete_json("system", "user")

        self.assertEqual(manager.models, ["fixture-model"])


if __name__ == "__main__":
    unittest.main()
