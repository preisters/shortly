from __future__ import annotations

import base64
import json
import unittest

from shortly.apps.personal.assets.fake import FakeImageProvider
from shortly.apps.personal.assets.google_video import GoogleVideoProvider
from shortly.apps.personal.assets.openai_image import OpenAIImageProvider
from shortly.core.domain.assets import MediaKind
from shortly.core.ports.assets import (
    ImageGenerationRequest,
    VideoGenerationRequest,
)


class _Interactions:
    def create(self, **kwargs):
        self.kwargs = kwargs
        return {
            "id": "video-1",
            "output_video": {
                "data": base64.b64encode(b"video").decode("ascii"),
                "mime_type": "video/mp4",
            },
        }


class _GoogleClient:
    def __init__(self):
        self.interactions = _Interactions()


class AssetProviderTests(unittest.TestCase):
    def test_openai_adapter_decodes_base64_and_sets_idempotency(self) -> None:
        seen = {}

        def transport(request, timeout):
            seen["headers"] = dict(request.header_items())
            seen["body"] = json.loads(request.data)
            return {
                "id": "image-1",
                "data": [{"b64_json": base64.b64encode(b"png").decode("ascii")}],
            }

        provider = OpenAIImageProvider("secret", transport=transport)
        asset = provider.generate(
            ImageGenerationRequest(
                prompt="fixture", idempotency_key="stable"
            )
        )
        self.assertEqual(asset.data, b"png")
        self.assertEqual(asset.media_kind, MediaKind.IMAGE)
        self.assertEqual(seen["body"]["model"], "gpt-image-2")
        self.assertEqual(seen["headers"]["Idempotency-key"], "stable")

    def test_google_adapter_uses_vertical_video_response(self) -> None:
        client = _GoogleClient()
        provider = GoogleVideoProvider("", client=client)
        submission = provider.submit(VideoGenerationRequest(prompt="fixture"))
        self.assertTrue(submission.completed)
        self.assertEqual(submission.asset.data, b"video")
        self.assertEqual(
            client.interactions.kwargs["response_format"]["aspect_ratio"],
            "9:16",
        )

    def test_fake_provider_is_deterministic_and_cost_free_by_default(self) -> None:
        provider = FakeImageProvider()
        asset = provider.generate(ImageGenerationRequest(prompt="fixture"))
        self.assertTrue(asset.data)
        self.assertEqual(asset.cost_usd, 0.0)
        self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
