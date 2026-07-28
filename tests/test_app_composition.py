from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import isolated_shortly_env

try:
    from fastapi.testclient import TestClient
    from shortly.apps.personal.api import create_app as create_personal_app
    from shortly.apps.team.api import create_app as create_team_app
    from shortly.config import Settings
    import shortly.web.app as legacy_app
    from shortly.product_profile import ProductKind
    from shortly.web.composition import composition_scope
    from shortly.web.factory import route_manifest
except ModuleNotFoundError:
    create_personal_app = None
    create_team_app = None


class _FakeService:
    def __init__(self) -> None:
        self.guide_called = threading.Event()

    def list_channels(self, settings=None):
        return [{"input": "@injected", "learned": True, "count": 1, "slug": "injected"}]

    def generate_guide(
        self,
        channel,
        text,
        is_script=False,
        settings=None,
        progress=None,
    ):
        self.guide_called.set()
        return {"ok": True, "channel": channel, "text": text}


@unittest.skipIf(
    create_team_app is None,
    "FastAPI runtime dependencies are unavailable",
)
class AppCompositionTests(unittest.TestCase):
    def test_legacy_web_command_preserves_data_and_adds_asset_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            legacy_data = Path(temporary) / "legacy-data"
            legacy_artifacts = Path(temporary) / "legacy-artifacts"
            with isolated_shortly_env(
                SHORTLY_DATA_DIR=str(legacy_data),
                SHORTLY_ARTIFACT_DIR=str(legacy_artifacts),
            ):
                legacy_settings = Settings.load()
                runtime_app = legacy_app._create_runtime_app(legacy_settings)
                automation = runtime_app.state.personal_automation["settings"]
                configured_product = os.environ.get("SHORTLY_PRODUCT")
                with TestClient(
                    runtime_app,
                    client=("127.0.0.1", 50000),
                ) as client:
                    response = client.get(
                        "/personal/assets",
                        params={
                            "channel": "@fixture",
                            "name": "saved-guide",
                        },
                    )

        self.assertIs(legacy_settings.product, ProductKind.LEGACY)
        self.assertIsNone(configured_product)
        self.assertEqual(legacy_settings.data_dir, legacy_data)
        self.assertEqual(automation.state_dir, legacy_data / "automation")
        self.assertEqual(automation.artifact_dir, legacy_artifacts)

        routes = set(route_manifest(runtime_app))
        self.assertEqual(runtime_app.state.product, ProductKind.PERSONAL.value)
        self.assertEqual(response.status_code, 200)
        self.assertIn(("GET", "/personal/assets"), routes)
        self.assertIn(
            ("POST", "/api/personal/manifests/from-guide"),
            routes,
        )

    def test_product_factories_share_legacy_routes_and_personal_adds_automation(self) -> None:
        with isolated_shortly_env():
            team = create_team_app()
            personal = create_personal_app()

        self.assertIsNot(team, personal)
        self.assertIsNot(team, legacy_app.app)
        self.assertEqual(team.state.product, ProductKind.TEAM.value)
        self.assertEqual(personal.state.product, ProductKind.PERSONAL.value)
        self.assertFalse(team.state.features.local_llm)
        self.assertTrue(personal.state.features.local_llm)
        legacy_routes = set(route_manifest(legacy_app.app))
        personal_routes = set(route_manifest(personal))
        self.assertTrue(legacy_routes.issubset(personal_routes))
        self.assertIn(("POST", "/api/personal/manifests"), personal_routes)
        self.assertIn(
            ("POST", "/api/personal/manifests/from-guide"),
            personal_routes,
        )
        self.assertIn(
            (
                "GET",
                "/api/personal/manifests/{manifest_id}/cuts/{cut_id}/stock-search",
            ),
            personal_routes,
        )
        self.assertIn(
            (
                "POST",
                "/api/personal/manifests/{manifest_id}/cuts/{cut_id}/stock-select",
            ),
            personal_routes,
        )
        self.assertNotIn(
            ("POST", "/api/personal/manifests"),
            set(route_manifest(team)),
        )
        self.assertNotIn(
            ("POST", "/api/personal/manifests/from-guide"),
            set(route_manifest(team)),
        )
        self.assertNotIn(("GET", "/api/local-models"), route_manifest(team))
        self.assertIn(("GET", "/api/local-models"), route_manifest(personal))
        self.assertEqual(len(team.routes), 23)
        self.assertGreater(len(personal.routes), len(legacy_app.app.routes))

    def test_team_http_surface_hides_local_provider_features(self) -> None:
        with isolated_shortly_env():
            app = create_team_app()
            team_settings = Settings.load()
            team_settings.lan_only = False
            with patch.object(
                legacy_app,
                "get_settings",
                return_value=team_settings,
            ):
                with TestClient(app) as client:
                    index_response = client.get("/")
                    local_response = client.get("/api/local-models")
                    settings_response = client.get("/api/settings")

        self.assertEqual(index_response.status_code, 200)
        self.assertNotIn('value="local"', index_response.text)
        self.assertNotIn("Ollama", index_response.text)
        self.assertEqual(local_response.status_code, 404)
        self.assertNotIn("local_base_url", settings_response.json())

    def test_personal_automation_api_is_loopback_only(self) -> None:
        with isolated_shortly_env():
            app = create_personal_app()
            personal_settings = Settings.load()
            personal_settings.lan_only = False
            with patch.object(
                legacy_app,
                "get_settings",
                return_value=personal_settings,
            ):
                with TestClient(app, client=("192.168.1.10", 50000)) as client:
                    blocked = client.get("/api/personal/automation/status")

        self.assertEqual(blocked.status_code, 403)

    def test_request_scope_selects_injected_service(self) -> None:
        fake = _FakeService()
        with isolated_shortly_env():
            app = create_team_app(service_api=fake)
            composition = app.state.shortly_composition
            with composition_scope(composition):
                channels = legacy_app.api_channels()

        self.assertEqual(channels[0]["input"], "@injected")

    def test_http_middleware_binds_injected_service(self) -> None:
        fake = _FakeService()
        with isolated_shortly_env():
            app = create_team_app(service_api=fake)
            with patch.object(
                legacy_app,
                "get_settings",
                return_value=Settings(lan_only=False),
            ):
                with TestClient(app) as client:
                    response = client.get("/api/channels")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["input"], "@injected")

    def test_background_job_captures_injected_service_before_thread_start(self) -> None:
        fake = _FakeService()
        with isolated_shortly_env():
            app = create_personal_app(service_api=fake)
            composition = app.state.shortly_composition
            with composition_scope(composition):
                response = legacy_app.api_guide(
                    legacy_app.GuideRequest(
                        channel="@fixture",
                        text="fixture topic",
                    )
                )

        self.assertTrue(fake.guide_called.wait(timeout=2.0))
        job_id = response["job_id"]
        deadline = time.time() + 2.0
        while legacy_app.JOBS[job_id].status == "running" and time.time() < deadline:
            time.sleep(0.01)
        try:
            self.assertEqual(legacy_app.JOBS[job_id].status, "done")
            self.assertEqual(
                legacy_app.JOBS[job_id].result,
                {
                    "ok": True,
                    "channel": "@fixture",
                    "text": "fixture topic",
                },
            )
        finally:
            legacy_app.JOBS.pop(job_id, None)

    def test_product_kind_is_not_left_changed_after_test_scope(self) -> None:
        old_product = os.environ.get("SHORTLY_PRODUCT")
        with isolated_shortly_env():
            create_team_app()
            self.assertEqual(os.environ["SHORTLY_PRODUCT"], "team")
        self.assertEqual(os.environ.get("SHORTLY_PRODUCT"), old_product)


if __name__ == "__main__":
    unittest.main()
