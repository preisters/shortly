from types import SimpleNamespace
import unittest

from shortly.llm.openai_transport import OpenAICompatibleClient


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


if __name__ == "__main__":
    unittest.main()
