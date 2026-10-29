from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import ROOT

try:
    import shortly.web.app as web_app
    from fastapi import HTTPException
except ModuleNotFoundError:
    web_app = None
    HTTPException = Exception

from shortly.config import Settings
from shortly.llm.base import LLMClient, LLMQuotaExceeded


EXPECTED_ROUTES = {
    ("GET", "/"),
    ("GET", "/api/status"),
    ("GET", "/api/channels"),
    ("GET", "/api/settings"),
    ("GET", "/api/local-models"),
    ("POST", "/api/settings"),
    ("POST", "/api/sfx/build"),
    ("POST", "/api/learn"),
    ("POST", "/api/guide"),
    ("POST", "/api/guide-revise"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/jobs/{job_id}/stream"),
    ("GET", "/api/profile"),
    ("GET", "/api/report"),
    ("GET", "/api/guides"),
    ("GET", "/api/guide-detail"),
    ("DELETE", "/api/guide-detail"),
    ("POST", "/api/guide-export-capcut"),
    ("GET", "/api/download"),
}


@unittest.skipIf(web_app is None, "FastAPI runtime dependencies are unavailable")
class WebContractTests(unittest.TestCase):
    def test_route_manifest_is_stable(self) -> None:
        routes = {
            (method, route.path)
            for route in web_app.app.routes
            for method in (getattr(route, "methods", None) or set())
            if route.path == "/" or route.path.startswith("/api/")
        }
        self.assertEqual(routes, EXPECTED_ROUTES)
        self.assertEqual(len(web_app.app.routes), 24)

    def test_settings_response_never_contains_api_key_values(self) -> None:
        settings = Settings(
            llm_provider="none",
            gemini_api_key="fixture-gemini-secret",
            openai_api_key="fixture-openai-secret",
            anthropic_api_key="fixture-anthropic-secret",
            typecast_api_key="fixture-typecast-secret",
        )
        with (
            patch.object(web_app, "get_settings", return_value=settings),
            patch.object(web_app, "get_llm", return_value=LLMClient()),
        ):
            response = web_app.api_get_settings()

        serialized = json.dumps(response, ensure_ascii=False)
        for secret in (
            settings.gemini_api_key,
            settings.openai_api_key,
            settings.anthropic_api_key,
            settings.typecast_api_key,
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(
            response["keys_set"],
            {
                "gemini": True,
                "openai": True,
                "anthropic": True,
                "typecast": True,
            },
        )

    def test_download_rejects_paths_outside_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as data_temp:
            with tempfile.TemporaryDirectory() as outside_temp:
                settings = SimpleNamespace(data_dir=Path(data_temp))
                outside = Path(outside_temp) / "private.txt"
                with patch.object(web_app, "get_settings", return_value=settings):
                    with self.assertRaises(HTTPException) as raised:
                        web_app.api_download(path=str(outside))
        self.assertEqual(raised.exception.status_code, 403)

    def test_lan_guard_classification(self) -> None:
        for address in ("127.0.0.1", "192.168.0.10", "10.0.0.5", "::1"):
            self.assertTrue(web_app._is_lan_ip(address))
        self.assertFalse(web_app._is_lan_ip("8.8.8.8"))
        self.assertFalse(web_app._is_lan_ip("not-an-ip"))

    def test_background_job_exposes_commercial_ai_quota_error_code(self) -> None:
        job = web_app.Job(id="fixture", kind="guide")

        def fail(_progress):
            raise LLMQuotaExceeded("anthropic")

        web_app._run_job(job, fail)

        self.assertEqual(job.status, "error")
        self.assertEqual(job.error_code, "commercial_ai_quota_exceeded")
        self.assertIn("토큰·크레딧", job.error)
        event = job.queue.get_nowait()
        self.assertEqual(event["type"], "error")
        self.assertEqual(event["error_code"], job.error_code)


if __name__ == "__main__":
    unittest.main()
