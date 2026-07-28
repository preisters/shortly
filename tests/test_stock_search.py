from __future__ import annotations

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from shortly.apps.personal.automation.stock_search import (
    OpenverseStockService,
    _validate_public_https_url,
)
from shortly.apps.personal.automation.store import AutomationStore
from shortly.core.domain.assets import AssetType, CandidateStatus
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest


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


if __name__ == "__main__":
    unittest.main()
