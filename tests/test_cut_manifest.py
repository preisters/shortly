from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from shortly.core.domain.assets import AssetType, CutManifest, MediaKind
from shortly.core.use_cases.build_cut_manifest import (
    build_cut_manifest,
    classify_asset_type,
)
from support import isolated_shortly_env

try:
    from fastapi.testclient import TestClient
    from shortly.apps.personal.api import create_app as create_personal_app
except ModuleNotFoundError:
    TestClient = None
    create_personal_app = None


def _guide() -> dict:
    return {
        "title": "fixture",
        "total_duration_sec": 4,
        "segments": [
            {
                "index": 0,
                "start_sec": 0,
                "duration_sec": 4,
                "narration": "상상 속 도시를 설명한다",
                "caption": "상상 속 도시",
                "cuts_suggested": 2,
                "image_suggestions": ["생성 일러스트", "화면 스크린샷"],
                "media_suggestions": ["사진", "사진"],
            }
        ],
    }


class CutManifestTests(unittest.TestCase):
    def test_guide_conversion_is_versioned_and_does_not_mutate_input(self) -> None:
        guide = _guide()
        before = repr(guide)
        manifest = build_cut_manifest(guide)

        self.assertEqual(repr(guide), before)
        self.assertEqual(manifest.schema_version, "shortly-cut-manifest-v1")
        self.assertEqual(len(manifest.cuts), 2)
        self.assertEqual(manifest.cuts[0].asset_type, AssetType.GENERATED_IMAGE)
        self.assertEqual(manifest.cuts[1].asset_type, AssetType.SCREENSHOT)
        self.assertEqual(manifest.cuts[0].duration_sec, 2.0)
        self.assertEqual(
            CutManifest.from_dict(manifest.to_dict()).to_dict(),
            manifest.to_dict(),
        )

    def test_fact_sensitive_rule_blocks_generation(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="실제 제품의 정확한 사진 생성",
            media_hint="사진",
            narration="공식 제품을 보여준다",
        )
        self.assertEqual(asset_type, AssetType.MANUAL_REQUIRED)
        self.assertEqual(media, MediaKind.IMAGE)
        self.assertTrue(fact_sensitive)
        self.assertTrue(notes)

    def test_explicit_override_is_recorded(self) -> None:
        manifest = build_cut_manifest(
            _guide(), overrides={"s000-c01": AssetType.MEME.value}
        )
        self.assertEqual(manifest.cuts[1].asset_type, AssetType.MEME)
        self.assertIn("사용자", manifest.cuts[1].notes[-1])

    @unittest.skipIf(TestClient is None, "FastAPI runtime unavailable")
    def test_personal_manifest_api_is_available_on_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with isolated_shortly_env(
                SHORTLY_USER_DATA=str(Path(temporary) / "state"),
                SHORTLY_DATA_DIR=str(Path(temporary) / "data"),
                SHORTLY_ARTIFACT_DIR=str(Path(temporary) / "artifacts"),
            ):
                app = create_personal_app()
                with TestClient(
                    app, client=("127.0.0.1", 50000)
                ) as client:
                    response = client.post(
                        "/api/personal/manifests",
                        json={"guide": _guide(), "overrides": {}},
                    )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["schema_version"], "shortly-cut-manifest-v1"
        )


if __name__ == "__main__":
    unittest.main()
