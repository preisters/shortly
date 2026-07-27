"""Commercial OpenAI provider adapter."""

from __future__ import annotations

from .openai_transport import OpenAICompatibleClient


class OpenAIClient(OpenAICompatibleClient):
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        text_model: str,
        vision_model: str,
    ) -> None:
        super().__init__(
            api_key,
            text_model,
            vision_model,
            provider=self.provider,
        )
