from __future__ import annotations

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from fastapi.testclient import TestClient
from shortly.apps.personal.api import create_app as create_personal_app
from shortly.apps.personal.automation.stock_search import (
    OpenverseStockService,
    PexelsStockService,
    PixabayStockService,
    StockSearchService,
    _validate_public_https_url,
)
from shortly.apps.personal.automation.store import AutomationStore
from shortly.core.domain.assets import (
    AssetCandidate,
    AssetType,
    CandidateStatus,
    MediaKind,
)
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest
from support import isolated_shortly_env


_RESULT_ID = "123e4567-e89b-42d3-a456-426614174000"


def _openverse_item(*, license_name: str = "cc0") -> dict:
    return {
        "id": _RESULT_ID,
        "title": "Tokyo market",
        "thumbnail": "https://images.example.test/thumb.jpg",
        "url": "https://images.example.test/original.jpg",
        "creator": "Fixture Artist",
        "creator_url": "https://example.test/artist",
        "license": license_name,
        "license_version": "1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "source": "fixture",
        "provider": "fixture",
        "foreign_landing_url": "https://example.test/image",
        "attribution": "Tokyo market by Fixture Artist",
        "width": 1600,
        "height": 1200,
        "mature": False,
    }


def _jpeg_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (640, 480), "#365e8d").save(stream, format="JPEG")
    return stream.getvalue()


class StockSearchTests(unittest.TestCase):
    def test_search_defaults_to_public_domain_and_cc0(self) -> None:
        payload = {
            "page_count": 2,
            "result_count": 2,
            "results": [
                _openverse_item(license_name="cc0"),
                {**_openverse_item(license_name="by-nc"), "id": _RESULT_ID[:-1] + "1"},
            ],
        }
        with patch(
            "shortly.apps.personal.automation.stock_search._request_json",
            return_value=payload,
        ) as request:
            result = OpenverseStockService(Path("assets")).search(
                "Tokyo market"
            )

        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(request.call_args.args[0]).query
        )
        self.assertEqual(query["license"], ["pdm,cc0"])
        self.assertEqual(query["mature"], ["false"])
        self.assertEqual(result["licenses"], ["pdm", "cc0"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["license"], "cc0")

    def test_search_can_explicitly_include_attribution_results(self) -> None:
        with patch(
            "shortly.apps.personal.automation.stock_search._request_json",
            return_value={"results": []},
        ) as request:
            result = OpenverseStockService(Path("assets")).search(
                "food",
                include_by=True,
            )

        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(request.call_args.args[0]).query
        )
        self.assertEqual(query["license"], ["pdm,cc0,by"])
        self.assertEqual(result["licenses"], ["pdm", "cc0", "by"])

    def test_pixabay_search_normalizes_site_and_caches_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "shortly.apps.personal.automation.stock_search._request_json",
            return_value={
                "totalHits": 1,
                "hits": [
                    {
                        "id": 123,
                        "tags": "시장, 음식",
                        "webformatURL": "https://cdn.example.test/pixabay.jpg",
                        "pageURL": "https://pixabay.com/photos/123/",
                        "user": "Fixture",
                        "imageWidth": 1200,
                        "imageHeight": 1800,
                    }
                ],
            },
        ) as request:
            service = PixabayStockService(Path(temporary), "pixabay-secret")
            first = service.search("시장")
            second = service.search("시장")
            cache_text = " ".join(
                path.read_text(encoding="utf-8")
                for path in Path(temporary).rglob("*.json")
            )

        self.assertEqual(request.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["results"][0]["provider"], "pixabay")
        self.assertEqual(
            first["results"][0]["provider_label"],
            "Pixabay",
        )
        called_url = request.call_args.args[0]
        self.assertIn("pixabay-secret", called_url)
        self.assertNotIn("pixabay-secret", cache_text)

    def test_pexels_search_uses_authorization_header_and_site_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "shortly.apps.personal.automation.stock_search._request_json",
            return_value={
                "total_results": 1,
                "photos": [
                    {
                        "id": 456,
                        "alt": "Market",
                        "src": {
                            "medium": "https://images.pexels.test/medium.jpg"
                        },
                        "url": "https://www.pexels.com/photo/456/",
                        "photographer": "Fixture",
                        "photographer_url": "https://www.pexels.com/@fixture",
                        "width": 1200,
                        "height": 1800,
                    }
                ],
            },
        ) as request:
            result = PexelsStockService(
                Path(temporary),
                "pexels-secret",
            ).search("market")

        self.assertEqual(
            request.call_args.kwargs["headers"]["Authorization"],
            "pexels-secret",
        )
        self.assertEqual(result["results"][0]["provider"], "pexels")
        self.assertEqual(result["results"][0]["provider_label"], "Pexels")

    def test_aggregate_search_skips_unconfigured_providers(self) -> None:
        service = StockSearchService(Path("assets"))
        with patch.object(
            service.providers["openverse"],
            "search",
            return_value={
                "provider": "openverse",
                "query": "food",
                "page": 1,
                "page_count": 1,
                "result_count": 1,
                "licenses": ["cc0"],
                "results": [
                    {
                        "id": _RESULT_ID,
                        "provider": "openverse",
                        "provider_label": "Openverse",
                    }
                ],
            },
        ):
            result = service.search("food", provider="all")

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["provider"], "openverse")
        statuses = {item["id"]: item for item in service.statuses()}
        self.assertTrue(statuses["openverse"]["available"])
        self.assertFalse(statuses["pixabay"]["available"])
        self.assertFalse(statuses["pexels"]["available"])

    def test_aggregate_selection_records_the_originating_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = AutomationStore(root / "automation.sqlite3")
            manifest = build_cut_manifest(
                {
                    "title": "fixture",
                    "segments": [
                        {
                            "duration_sec": 2,
                            "image_suggestions": ["시장 사진"],
                        }
                    ],
                }
            )
            cut = manifest.cuts[0]
            cut.asset_type = AssetType.STOCK_IMAGE
            store.save_manifest(manifest)
            service = StockSearchService(
                root / "assets",
                pixabay_api_key="fixture-key",
            )
            pixabay = service.providers["pixabay"]
            with patch.object(
                pixabay,
                "detail",
                return_value={
                    "id": "123",
                    "title": "Market",
                    "creator": "Fixture",
                    "license": "pixabay",
                    "license_label": "Pixabay Content License",
                    "license_url": "https://pixabay.com/service/license-summary/",
                    "source": "Pixabay",
                    "provider": "pixabay",
                    "provider_label": "Pixabay",
                    "landing_url": "https://pixabay.com/photos/123/",
                    "attribution": "Fixture · Pixabay",
                    "download_url": "https://cdn.example.test/image.jpg",
                },
            ), patch(
                "shortly.apps.personal.automation.stock_search._download_image",
                return_value=(
                    _jpeg_bytes(),
                    "https://cdn.example.test/image.jpg",
                ),
            ):
                service.select(
                    store,
                    provider="pixabay",
                    manifest_id=manifest.manifest_id,
                    cut_id=cut.id,
                    result_id="123",
                    query="market",
                )

            selected = store.load_manifest(manifest.manifest_id).cut(
                cut.id
            ).selected_candidate

        self.assertIsNotNone(selected)
        self.assertEqual(selected.provider, "pixabay")
        self.assertEqual(selected.metadata["provider_label"], "Pixabay")
        self.assertEqual(selected.metadata["source"], "Pixabay")

    def test_private_or_non_https_download_urls_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _validate_public_https_url("http://example.com/image.jpg")
        with patch(
            "shortly.apps.personal.automation.stock_search.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("127.0.0.1", 443)),
            ],
        ):
            with self.assertRaises(ValueError):
                _validate_public_https_url(
                    "https://images.example.test/image.jpg"
                )

    def test_selected_stock_image_is_stored_as_capcut_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = AutomationStore(root / "automation.sqlite3")
            manifest = build_cut_manifest(
                {
                    "title": "fixture",
                    "segments": [
                        {
                            "index": 0,
                            "duration_sec": 2,
                            "image_suggestions": ["도쿄 시장 전경 자료 사진"],
                        }
                    ],
                }
            )
            cut = manifest.cuts[0]
            cut.asset_type = AssetType.STOCK_IMAGE
            store.save_manifest(manifest)
            service = OpenverseStockService(root / "assets")

            with patch.object(
                service,
                "detail",
                return_value=_openverse_item(),
            ), patch(
                "shortly.apps.personal.automation.stock_search._download_image",
                return_value=(
                    _jpeg_bytes(),
                    "https://images.example.test/original.jpg",
                ),
            ):
                result = service.select(
                    store,
                    manifest_id=manifest.manifest_id,
                    cut_id=cut.id,
                    result_id=_RESULT_ID,
                    query="Tokyo market",
                )

            saved_cut = store.load_manifest(manifest.manifest_id).cut(cut.id)
            candidate = saved_cut.selected_candidate
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate.provider, "openverse")
            self.assertEqual(candidate.status, CandidateStatus.SELECTED)
            self.assertEqual(candidate.cost_usd, 0.0)
            self.assertEqual(candidate.metadata["license"], "cc0")
            self.assertEqual(candidate.metadata["search_query"], "Tokyo market")
            self.assertTrue(Path(candidate.original_path).is_file())
            self.assertEqual(
                result["candidate"]["id"],
                saved_cut.selected_candidate_id,
            )

    def test_selected_asset_preview_serves_only_personal_asset_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with isolated_shortly_env(
                SHORTLY_USER_DATA=str(root / "state"),
                SHORTLY_DATA_DIR=str(root / "data"),
                SHORTLY_ARTIFACT_DIR=str(root / "artifacts"),
            ):
                app = create_personal_app()
                automation = app.state.personal_automation
                store = automation["store"]
                assets_dir = automation["settings"].assets_dir
                manifest = build_cut_manifest(
                    {
                        "title": "fixture",
                        "segments": [
                            {
                                "index": 0,
                                "duration_sec": 2,
                                "image_suggestions": ["자료 이미지"],
                            }
                        ],
                    }
                )
                cut = manifest.cuts[0]
                preview = (
                    assets_dir
                    / manifest.manifest_id
                    / cut.id
                    / "selected.jpg"
                )
                preview.parent.mkdir(parents=True)
                preview.write_bytes(_jpeg_bytes())
                candidate = AssetCandidate(
                    provider="fixture",
                    model="fixture",
                    media_kind=MediaKind.IMAGE,
                    status=CandidateStatus.SELECTED,
                    original_path=str(preview),
                    prepared_path=str(preview),
                    mime_type="image/jpeg",
                )
                cut.candidates.append(candidate)
                cut.selected_candidate_id = candidate.id
                store.save_manifest(manifest)

                with TestClient(
                    app,
                    client=("127.0.0.1", 50000),
                ) as client:
                    response = client.get(
                        f"/api/personal/manifests/{manifest.manifest_id}"
                        f"/cuts/{cut.id}/preview"
                    )

                outside = root / "outside.jpg"
                outside.write_bytes(_jpeg_bytes())
                candidate.original_path = str(outside)
                candidate.prepared_path = str(outside)
                store.save_manifest(manifest)
                with TestClient(
                    app,
                    client=("127.0.0.1", 50000),
                ) as client:
                    blocked = client.get(
                        f"/api/personal/manifests/{manifest.manifest_id}"
                        f"/cuts/{cut.id}/preview"
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertTrue(response.content.startswith(b"\xff\xd8"))
        self.assertEqual(blocked.status_code, 400)


if __name__ == "__main__":
    unittest.main()
