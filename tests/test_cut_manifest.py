from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from shortly.core.domain.assets import AssetType, CutManifest, MediaKind
from shortly.core.use_cases.build_cut_manifest import (
    build_cut_manifest,
    classify_asset_type,
)
from shortly.generate.image_reco import recommend_cuts
from shortly.llm.base import LLMClient
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


class _SavedGuideService:
    def get_guide(self, channel: str, name: str) -> dict | None:
        if channel != "@fixture" or name != "saved-guide":
            return None
        return {"guide": _guide()}


class _AssetDirectorLlm(LLMClient):
    available = True
    text_model = "fixture-generation-model"

    def complete_json(self, *args, **kwargs):
        return {
            "segments": [
                {
                    "index": 0,
                    "image_suggestions": [
                        "일본 바이어 협상 장면 재현",
                        "실제 제품 포장 사진",
                    ],
                    "media_suggestions": ["사진", "사진"],
                    "effect_suggestions": ["줌 인", "없음"],
                    "asset_type_suggestions": [
                        "generated_image",
                        "real_image",
                    ],
                    "fact_sensitive_suggestions": [False, True],
                    "asset_reason_suggestions": [
                        "실제 기록사진이 아닌 협상 상황 재현 장면",
                        "실제 포장 외형과 문구가 정확해야 함",
                    ],
                }
            ]
        }


class CutManifestTests(unittest.TestCase):
    def test_personal_asset_console_exposes_safe_batch_generation(self) -> None:
        page = (
            Path(__file__).resolve().parents[1]
            / "shortly"
            / "apps"
            / "personal"
            / "static"
            / "assets.html"
        ).read_text(encoding="utf-8")

        self.assertIn('id="generate-all"', page)
        self.assertIn("pendingGeneratedCuts()", page)
        self.assertIn("!c.fact_sensitive", page)
        self.assertIn("!c.selected_candidate_id", page)
        self.assertIn(
            "for(let index=0;index<configured.length;index++)",
            page,
        )
        self.assertIn("summary.failures.push", page)

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
        self.assertEqual(asset_type, AssetType.REAL_IMAGE)
        self.assertEqual(media, MediaKind.IMAGE)
        self.assertTrue(fact_sensitive)
        self.assertTrue(notes)

    def test_unique_non_factual_scene_defaults_to_generated_image(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="황금알을 낳는 거위가 수출 시장을 걷어차는 장면",
            media_hint="사진",
            narration="비유를 시각적으로 보여준다",
        )
        self.assertEqual(asset_type, AssetType.GENERATED_IMAGE)
        self.assertEqual(media, MediaKind.IMAGE)
        self.assertFalse(fact_sensitive)
        self.assertIn("AI 이미지", notes[0])

    def test_generic_reference_scene_defaults_to_stock_image(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="도쿄 시장 전경 자료 사진",
            media_hint="사진",
            narration="시장 규모를 소개한다",
        )
        self.assertEqual(asset_type, AssetType.STOCK_IMAGE)
        self.assertEqual(media, MediaKind.IMAGE)
        self.assertFalse(fact_sensitive)
        self.assertIn("스톡", notes[0])

    def test_non_factual_motion_scene_defaults_to_generated_video(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="김 캐릭터가 세계 지도를 달리는 과장된 장면",
            media_hint="영상/움짤",
            narration="성장세를 재미있게 표현한다",
        )
        self.assertEqual(asset_type, AssetType.GENERATED_VIDEO)
        self.assertEqual(media, MediaKind.VIDEO)
        self.assertFalse(fact_sensitive)
        self.assertIn("AI 영상", notes[0])

    def test_recreated_people_scene_is_not_treated_as_real_portrait(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="일본 바이어 미팅 또는 협상 장면 연상 인물 사진",
            media_hint="사진",
            narration="실제 협상 일화를 설명한다",
        )
        self.assertEqual(asset_type, AssetType.GENERATED_IMAGE)
        self.assertEqual(media, MediaKind.IMAGE)
        self.assertFalse(fact_sensitive)
        self.assertIn("AI 이미지", notes[0])

    def test_generation_llm_classification_has_priority(self) -> None:
        guide = {
            "title": "fixture",
            "total_duration_sec": 4,
            "segments": [
                {
                    "index": 0,
                    "start_sec": 0,
                    "duration_sec": 4,
                    "narration": "실제 사건을 두 컷으로 설명한다",
                    "caption": "협상과 제품",
                    "cuts_suggested": 2,
                }
            ],
        }
        classified = recommend_cuts(
            _AssetDirectorLlm(),
            {"image_style": {}, "editing_style": {}},
            guide,
        )
        manifest = build_cut_manifest(classified)

        self.assertEqual(
            classified["segments"][0]["asset_classification_source"],
            "generation_llm",
        )
        self.assertEqual(
            manifest.cuts[0].asset_type,
            AssetType.GENERATED_IMAGE,
        )
        self.assertFalse(manifest.cuts[0].fact_sensitive)
        self.assertEqual(
            manifest.cuts[1].asset_type,
            AssetType.REAL_IMAGE,
        )
        self.assertTrue(manifest.cuts[1].fact_sensitive)
        self.assertIn("생성 모델 판단", manifest.cuts[0].notes[0])

    def test_exact_product_source_overrides_unsafe_model_generation(self) -> None:
        guide = {
            "title": "fixture",
            "total_duration_sec": 2,
            "segments": [
                {
                    "index": 0,
                    "start_sec": 0,
                    "duration_sec": 2,
                    "narration": "실제 제품을 소개한다",
                    "caption": "제품",
                    "cuts_suggested": 1,
                    "image_suggestions": ["실제 제품 포장지 클로즈업 사진"],
                    "media_suggestions": ["사진"],
                    "asset_type_suggestions": ["generated_image"],
                    "fact_sensitive_suggestions": [False],
                    "asset_reason_suggestions": ["생성 이미지가 편리함"],
                }
            ],
        }
        cut = build_cut_manifest(guide).cuts[0]

        self.assertEqual(cut.asset_type, AssetType.REAL_IMAGE)
        self.assertTrue(cut.fact_sensitive)
        self.assertTrue(any("안전 규칙" in note for note in cut.notes))

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

    @unittest.skipIf(TestClient is None, "FastAPI runtime unavailable")
    def test_saved_guide_handoff_reuses_the_existing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with isolated_shortly_env(
                SHORTLY_USER_DATA=str(Path(temporary) / "state"),
                SHORTLY_DATA_DIR=str(Path(temporary) / "data"),
                SHORTLY_ARTIFACT_DIR=str(Path(temporary) / "artifacts"),
            ):
                app = create_personal_app(service_api=_SavedGuideService())
                with TestClient(
                    app, client=("127.0.0.1", 50000)
                ) as client:
                    first = client.post(
                        "/api/personal/manifests/from-guide",
                        json={
                            "channel": "@fixture",
                            "name": "saved-guide",
                        },
                    )
                    store = app.state.personal_automation["store"]
                    old_manifest = store.load_manifest(
                        first.json()["manifest"]["manifest_id"]
                    )
                    old_manifest.metadata.pop(
                        "asset_classifier_version",
                        None,
                    )
                    old_manifest.cuts[0].asset_type = AssetType.STOCK_IMAGE
                    store.save_manifest(old_manifest)
                    second = client.post(
                        "/api/personal/manifests/from-guide",
                        json={
                            "channel": "@fixture",
                            "name": "saved-guide",
                        },
                    )
                    third = client.post(
                        "/api/personal/manifests/from-guide",
                        json={
                            "channel": "@fixture",
                            "name": "saved-guide",
                        },
                    )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_body = first.json()
        second_body = second.json()
        self.assertFalse(first_body["reused"])
        self.assertTrue(second_body["reused"])
        self.assertTrue(second_body["defaults_refreshed"])
        self.assertTrue(third.json()["reused"])
        self.assertFalse(third.json()["defaults_refreshed"])
        self.assertEqual(
            first_body["manifest"]["manifest_id"],
            second_body["manifest"]["manifest_id"],
        )
        self.assertEqual(
            second_body["manifest"]["cuts"][0]["asset_type"],
            AssetType.GENERATED_IMAGE.value,
        )
        self.assertEqual(
            first_body["manifest"]["metadata"]["source_channel"],
            "@fixture",
        )
        self.assertEqual(first_body["guide"]["title"], "fixture")


if __name__ == "__main__":
    unittest.main()
