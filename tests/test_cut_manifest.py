from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from shortly.core.domain.assets import (
    AssetCandidate,
    AssetType,
    CandidateStatus,
    CutManifest,
    MediaKind,
)
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
                    "stock_query_suggestions": ["", ""],
                    "person_depiction_suggestions": [
                        "fictional_person",
                        "none",
                    ],
                    "ai_disclosure_suggestions": [True, False],
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
        self.assertIn("로컬 Qwen 검수 중", page)
        self.assertIn("data-cut-status", page)
        self.assertIn("jobs.some(job=>", page)
        self.assertIn(
            "jobRefreshTimer=setTimeout(pollJobStatuses,2000)",
            page,
        )
        self.assertNotIn("setInterval(refreshJobStatuses,2000)", page)
        self.assertIn('setCutState(cut.id,"running")', page)
        self.assertIn('data-stock-search="', page)
        self.assertIn("/stock-search?", page)
        self.assertIn("/stock-select", page)
        self.assertIn("cut.stock_query||cut.prompt", page)
        self.assertIn("가상 인물 고지", page)
        self.assertIn("CC BY 결과도 포함", page)
        self.assertIn("현재 선택은 해제됩니다", page)
        self.assertIn("el.value=cut.asset_type", page)
        self.assertIn("스톡 이미지가 선택되지 않은 컷이 있습니다", page)
        self.assertIn("<th>미리보기</th>", page)
        self.assertIn("/preview?v=", page)
        self.assertIn("AI 이미지", page)
        self.assertNotIn("AI 이미지 생성 · 권장", page)
        self.assertIn(
            'replace(/^생성 모델 판단\\s*[:：]\\s*/,"")',
            page,
        )

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

    def test_korean_local_scene_does_not_default_to_stock_image(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="한국 편의점 내부 자료 사진",
            media_hint="사진",
            narration="한국 편의점의 독특한 분위기를 보여준다",
        )
        self.assertEqual(asset_type, AssetType.GENERATED_IMAGE)
        self.assertEqual(media, MediaKind.IMAGE)
        self.assertFalse(fact_sensitive)
        self.assertIn("AI 이미지", notes[0])

    def test_common_korean_landmark_can_still_use_stock_image(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="서울 야경 자료 사진",
            media_hint="사진",
            narration="서울을 소개한다",
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

    def test_generated_chart_is_blocked_from_ai_generation(self) -> None:
        asset_type, media, fact_sensitive, notes = classify_asset_type(
            prompt="2023년 수출액 4억 달러 상승 그래프",
            media_hint="사진",
            narration="검증된 수치를 설명한다",
        )
        self.assertEqual(asset_type, AssetType.MANUAL_REQUIRED)
        self.assertEqual(media, MediaKind.IMAGE)
        self.assertTrue(fact_sensitive)
        self.assertIn("생성형 AI", notes[0])

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
        self.assertEqual(
            manifest.cuts[0].person_depiction,
            "fictional_person",
        )
        self.assertIn(
            "실존 인물이 아닙니다",
            manifest.cuts[0].disclosure_text,
        )

    def test_private_company_representative_uses_fictional_stand_in(self) -> None:
        guide = {
            "title": "fixture",
            "segments": [
                {
                    "index": 0,
                    "duration_sec": 2,
                    "cuts_suggested": 1,
                    "narration": "대표가 단호하게 말했다",
                    "image_suggestions": ["지도표 성경김 대표 인물 사진"],
                    "media_suggestions": ["사진"],
                    "asset_type_suggestions": ["real_image"],
                    "fact_sensitive_suggestions": [True],
                    "asset_reason_suggestions": ["실존 대표"],
                }
            ],
        }

        cut = build_cut_manifest(guide).cuts[0]

        self.assertEqual(cut.asset_type, AssetType.GENERATED_IMAGE)
        self.assertFalse(cut.fact_sensitive)
        self.assertEqual(cut.person_depiction, "fictional_person")
        self.assertIn("가상의 한국인 기업 대표", cut.prompt)
        self.assertIn("실존 인물이 아닙니다", cut.disclosure_text)

    def test_private_company_representative_video_keeps_video_with_disclosure(
        self,
    ) -> None:
        guide = {
            "title": "fixture",
            "segments": [
                {
                    "index": 0,
                    "duration_sec": 2,
                    "cuts_suggested": 1,
                    "narration": "대표가 단호하게 말했다",
                    "image_suggestions": ["비공인 회사 대표가 말하는 영상"],
                    "media_suggestions": ["영상"],
                    "asset_type_suggestions": ["generated_video"],
                    "person_depiction_suggestions": ["private_person"],
                    "fact_sensitive_suggestions": [False],
                    "asset_reason_suggestions": ["비공인 실존 대표 재연"],
                }
            ],
        }

        cut = build_cut_manifest(guide).cuts[0]

        self.assertEqual(cut.asset_type, AssetType.GENERATED_VIDEO)
        self.assertEqual(cut.media_kind, MediaKind.VIDEO)
        self.assertEqual(cut.person_depiction, "fictional_person")
        self.assertIn("실존 인물이 아닙니다", cut.disclosure_text)

    def test_model_generated_numeric_card_is_safety_blocked(self) -> None:
        guide = {
            "title": "fixture",
            "segments": [
                {
                    "index": 0,
                    "duration_sec": 2,
                    "cuts_suggested": 1,
                    "narration": "2023년 수출액을 설명한다",
                    "image_suggestions": ["수출액 4억 달러 수치 강조 그래픽"],
                    "media_suggestions": ["사진"],
                    "asset_type_suggestions": ["generated_image"],
                    "fact_sensitive_suggestions": [False],
                    "asset_reason_suggestions": ["정보 그래픽"],
                }
            ],
        }

        cut = build_cut_manifest(guide).cuts[0]

        self.assertEqual(cut.asset_type, AssetType.MANUAL_REQUIRED)
        self.assertTrue(cut.fact_sensitive)
        self.assertTrue(any("환각 위험" in note for note in cut.notes))

    def test_generation_llm_stock_query_is_carried_to_manifest(self) -> None:
        llm = _AssetDirectorLlm()
        original = llm.complete_json

        def with_stock_query(*args, **kwargs):
            payload = original(*args, **kwargs)
            segment = payload["segments"][0]
            segment["asset_type_suggestions"][0] = "stock_image"
            segment["image_suggestions"][0] = "도쿄 시장 전경"
            segment["stock_query_suggestions"][0] = "Tokyo market street"
            return payload

        llm.complete_json = with_stock_query
        classified = recommend_cuts(
            llm,
            {"image_style": {}, "editing_style": {}},
            {
                "title": "fixture",
                "segments": [
                    {
                        "index": 0,
                        "duration_sec": 2,
                        "narration": "도쿄 시장을 소개한다",
                        "cuts_suggested": 2,
                    }
                ],
            },
        )
        manifest = build_cut_manifest(classified)

        self.assertEqual(
            classified["segments"][0]["stock_query_suggestions"][0],
            "Tokyo market street",
        )
        self.assertEqual(
            manifest.cuts[0].stock_query,
            "Tokyo market street",
        )

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
    def test_type_change_clears_selection_but_preserves_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with isolated_shortly_env(
                SHORTLY_USER_DATA=str(Path(temporary) / "state"),
                SHORTLY_DATA_DIR=str(Path(temporary) / "data"),
                SHORTLY_ARTIFACT_DIR=str(Path(temporary) / "artifacts"),
            ):
                app = create_personal_app()
                store = app.state.personal_automation["store"]
                manifest = build_cut_manifest(_guide())
                cut = manifest.cuts[0]
                cut.person_depiction = "fictional_person"
                cut.disclosure_text = "실존 인물이 아닙니다."
                candidate = AssetCandidate(
                    provider="fixture",
                    model="fixture",
                    media_kind=MediaKind.IMAGE,
                    status=CandidateStatus.SELECTED,
                )
                cut.candidates.append(candidate)
                cut.selected_candidate_id = candidate.id
                store.save_manifest(manifest)

                with TestClient(
                    app, client=("127.0.0.1", 50000)
                ) as client:
                    response = client.patch(
                        f"/api/personal/manifests/{manifest.manifest_id}"
                        f"/cuts/{cut.id}",
                        json={"asset_type": AssetType.STOCK_IMAGE.value},
                    )

                saved = store.load_manifest(manifest.manifest_id).cut(cut.id)
                history = store.selection_history(manifest.manifest_id)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(saved.selected_candidate_id)
        self.assertEqual(saved.disclosure_text, "")
        self.assertEqual(len(saved.candidates), 1)
        self.assertEqual(saved.candidates[0].id, candidate.id)
        self.assertEqual(saved.candidates[0].status, CandidateStatus.READY)
        self.assertTrue(history[0]["metadata"]["selection_cleared"])

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
