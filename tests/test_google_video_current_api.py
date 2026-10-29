"""Cost-free wire tests for the current Omni REST contract."""
import base64
import json
import unittest

import httpx

from shortly.apps.personal.assets.google_video import GoogleVideoProvider, google_error_details
from shortly.core.ports.assets import AssetGenerationError, VideoGenerationRequest


class GoogleVideoCurrentAPITests(unittest.TestCase):
    def test_two_images_fixed_config_and_current_rest_steps(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json={
                "id": "fixture-id", "status": "completed",
                "steps": [
                    {"type": "user_input", "content": [{"type": "video", "data": "wrong"}]},
                    {"type": "model_output", "content": [{"type": "video",
                     "mime_type": "video/mp4", "data": base64.b64encode(b"video").decode()}]},
                ],
            })

        provider = GoogleVideoProvider("secret", transport=httpx.MockTransport(handle))
        result = provider.submit(VideoGenerationRequest(
            prompt="[# Sources <FIRST_FRAME>@Image1 <LAST_FRAME>@Image2] test",
            source_image=b"first", end_image=b"last", duration_seconds=10,
        ))
        self.assertEqual(len(seen), 1)
        self.assertEqual(str(seen[0].url), "https://generativelanguage.googleapis.com/v1beta/interactions")
        self.assertEqual(seen[0].headers["x-goog-api-key"], "secret")
        body = json.loads(seen[0].content)
        self.assertEqual(body["model"], "gemini-omni-1.1-flash")
        self.assertEqual([x["type"] for x in body["input"]], ["image", "image", "text"])
        self.assertEqual(base64.b64decode(body["input"][0]["data"]), b"first")
        self.assertEqual(base64.b64decode(body["input"][1]["data"]), b"last")
        self.assertEqual(body["response_format"], {
            "type": "video", "aspect_ratio": "9:16", "duration": "4s",
            "resolution": "720p", "delivery": "inline",
        })
        self.assertTrue(result.completed)
        self.assertEqual(result.asset.data, b"video")

    def test_http_failures_never_retry_even_with_retry_header(self):
        for status in (400, 429, 500, 503, 307):
            with self.subTest(status=status):
                seen = []

                def handle(request):
                    seen.append(request)
                    return httpx.Response(status, headers={
                        "x-should-retry": "true", "x-request-id": "fixture-request",
                        "location": "https://example.invalid/do-not-follow",
                    }, json={"error": {"code": "content_blocked", "message": "blocked secret"}})

                provider = GoogleVideoProvider("secret", transport=httpx.MockTransport(handle))
                with self.assertRaises(AssetGenerationError) as caught:
                    provider.submit(VideoGenerationRequest(prompt="fixture"))
                self.assertEqual(len(seen), 1)
                self.assertEqual(caught.exception.http_status, status)
                self.assertNotIn("secret", str(caught.exception))
                diagnostic = google_error_details(caught.exception.__cause__, "secret")
                self.assertEqual(diagnostic["provider_code"], "content_blocked")
                self.assertEqual(diagnostic["request_id"], "fixture-request")
                self.assertNotIn("secret", diagnostic["provider_message"])
                if status >= 500:
                    self.assertEqual(caught.exception.code, "provider_server_error")

    def test_network_failure_never_resubmits(self):
        seen = []

        def handle(request):
            seen.append(request)
            raise httpx.ReadTimeout("timed out", request=request)

        provider = GoogleVideoProvider("secret", transport=httpx.MockTransport(handle))
        with self.assertRaises(AssetGenerationError):
            provider.submit(VideoGenerationRequest(prompt="fixture"))
        self.assertEqual(len(seen), 1)

    def test_end_without_start_is_rejected_before_network(self):
        provider = GoogleVideoProvider("secret", transport=httpx.MockTransport(
            lambda _: self.fail("must not submit an invalid interpolation request")))
        with self.assertRaises(AssetGenerationError) as caught:
            provider.submit(VideoGenerationRequest(prompt="fixture", end_image=b"last"))
        self.assertEqual(caught.exception.code, "invalid_request")


if __name__ == "__main__":
    unittest.main()
