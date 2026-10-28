from types import SimpleNamespace
import unittest

from shortly.llm.openai_transport import OpenAICompatibleClient
from shortly.llm.base import LLMError, LLMQuotaExceeded


class _Completions:
    def __init__(self) -> None:
        self.kwargs = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(
            content="",
            reasoning_content='{"passed": true}',
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FailingCompletions:
    def __init__(self, message: str) -> None:
        self.message = message

    def create(self, **_kwargs):
        raise RuntimeError(self.message)


class OpenAICompatibleTransportTests(unittest.TestCase):
    def test_local_json_request_uses_json_object_mode(self) -> None:
        completions = _Completions()
        client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        client.text_model = "local-model"
        client.vision_model = "local-model"
        client._base_url = "http://127.0.0.1:1234/v1"
        client._no_think = True
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        value = client.complete_json("system", "user", max_tokens=100)

        self.assertEqual(value, {"passed": True})
        self.assertEqual(
            completions.kwargs["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": {},
                },
            },
        )
        self.assertIn("/no_think", completions.kwargs["messages"][0]["content"])

    def test_commercial_openai_quota_error_is_normalized(self) -> None:
        client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        client.text_model = "commercial-model"
        client.vision_model = "commercial-model"
        client._base_url = ""
        client._no_think = False
        client._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=_FailingCompletions("insufficient_quota")
            )
        )

        with self.assertRaises(LLMQuotaExceeded):
            client.complete_text("system", "user")

    def test_local_resource_error_is_not_reported_as_commercial_quota(self) -> None:
        client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        client.text_model = "local-model"
        client.vision_model = "local-model"
        client._base_url = "http://127.0.0.1:1234/v1"
        client._no_think = True
        client._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=_FailingCompletions("RESOURCE_EXHAUSTED")
            )
        )

        with self.assertRaises(LLMError) as raised:
            client.complete_text("system", "user")

        self.assertNotIsInstance(raised.exception, LLMQuotaExceeded)


if __name__ == "__main__":
    unittest.main()
