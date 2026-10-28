from __future__ import annotations

import re
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

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
from shortly.generate.image_reco import _profile_specific_terms, recommend_cuts
from shortly.llm.base import LLMClient, LLMError
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
                    "visual_role_suggestions": [
                        "갈등: 일본 바이어와의 협상",
                        "쟁점: 실제 제품 포장 확인",
                    ],
                    "visible_text_suggestions": [
                        "없음",
                        "실제 제품 원본 문구 유지",
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


class _FailingAssetDirectorLlm(LLMClient):
    available = True
    text_model = "fixture-failing-model"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        raise LLMError("fixture parse failure")


class _RetryingAssetDirectorLlm(_AssetDirectorLlm):
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise LLMError("first response was truncated")
        return super().complete_json(*args, **kwargs)


class _RepairingStockQueryLlm(_AssetDirectorLlm):
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        payload = super().complete_json(*args, **kwargs)
        segment = payload["segments"][0]
        segment["asset_type_suggestions"][0] = "stock_image"
        segment["image_suggestions"][0] = "베트남 도심의 무더운 거리"
        segment["stock_query_suggestions"][0] = (
            "베트남 무더운 거리"
            if self.calls == 1
            else "Vietnam tropical city street"
        )
        segment["person_depiction_suggestions"][0] = "none"
        segment["ai_disclosure_suggestions"][0] = False
        return payload


class _BatchAssetDirectorLlm(LLMClient):
    available = True
    text_model = "fixture-batch-model"

    def __init__(self) -> None:
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        requested = [
            (int(index), int(cuts))
            for index, cuts in re.findall(r"- index (\d+): 컷 (\d+)개", user)
        ]
        self.calls.append(requested)
        items = []
        for index, cuts in requested:
            items.append(
                {
                    "index": index,
                    "image_suggestions": [f"현재 주제 장면 {index}-{cut}" for cut in range(cuts)],
                    "media_suggestions": ["사진"] * cuts,
                    "effect_suggestions": ["없음"] * cuts,
                    "asset_type_suggestions": ["generated_image"] * cuts,
                    "fact_sensitive_suggestions": [False] * cuts,
                    "asset_reason_suggestions": ["현재 대본 재연"] * cuts,
                    "visual_role_suggestions": ["현재 이야기 전개"] * cuts,
                    "visible_text_suggestions": ["없음"] * cuts,
                    "stock_query_suggestions": [""] * cuts,
                    "person_depiction_suggestions": ["none"] * cuts,
                    "ai_disclosure_suggestions": [False] * cuts,
                }
            )
        return {"segments": items}


class CutManifestTests(unittest.TestCase):
    def test_asset_director_receives_story_context_and_visual_coverage_rules(
        self,
    ) -> None:
        class CapturingLlm(_AssetDirectorLlm):
            def complete_json(self, system, user, **kwargs):
                self.system = system
                self.user = user
                return super().complete_json(system, user, **kwargs)

        llm = CapturingLlm()
        guide = {
            "title": "독도 때문에 일본시장 포기한 회사",
            "hook": "일본 수출보다 독도 표기를 택했다",
            "segments": [
                {
                    "index": 0,
                    "duration_sec": 4,
                    "intent": "결과 반전",
                    "caption": "독도 표기를 지켰다",
                    "narration": (
                        "일본이 큰 수출 시장이지만 독도 표기를 지우느니 "
                        "일본 수출을 포기했다"
                    ),
                    "cuts_suggested": 2,
                }
            ],
        }

        classified = recommend_cuts(
            llm,
            {"image_style": {}, "editing_style": {}},
            guide,
        )

        self.assertIn("독도 때문에 일본시장 포기한 회사", llm.user)
        self.assertIn("일본 수출보다 독도 표기를 택했다", llm.user)
        self.assertIn("무음 테스트", llm.user)
        self.assertIn("범용 B-roll은 세그먼트당 최대 한 컷", llm.user)
        self.assertIn("visual_role_suggestions", llm.user)
        self.assertIn("visible_text_suggestions", llm.user)
        self.assertIn("화면 글자 지시:", llm.user)
        self.assertIn("모든 포장의 색, 도형, 라벨 위치, 철자와 서체", llm.user)
        self.assertIn("대표 행동 하나만", llm.user)
        self.assertIn("음식의 김·수증기는 기본 연출에서 제외", llm.user)
        self.assertIn("전자레인지로 데운 김밥", llm.user)
        self.assertIn("음식 종류나 '데웠다'는 언급만으로 예외", llm.user)
        self.assertIn("약하고 자연스러운 김을 허용", llm.user)
        self.assertIn("주요 인물 한 명, 주요 소품 한 종류", llm.user)
        self.assertIn("손·입·소품이 연속으로 바뀌는 다단계", llm.user)
        self.assertIn("다른 물체로 변하거나 합쳐지거나 사라지는 장면", llm.user)
        self.assertEqual(
            classified["segments"][0]["visual_role_suggestions"],
            ["갈등: 일본 바이어와의 협상", "쟁점: 실제 제품 포장 확인"],
        )
        self.assertIn(
            "화면 글자 지시: 읽을 수 있는 글자 없음",
            classified["segments"][0]["image_suggestions"][0],
        )

    def test_asset_director_retries_a_failed_batch_once(self) -> None:
        llm = _RetryingAssetDirectorLlm()
        guide = {
            "title": "현재 주제",
            "segments": [
                {
                    "index": 0,
                    "duration_sec": 4,
                    "narration": "현재 사건을 설명한다",
                    "cuts_suggested": 2,
                }
            ],
        }

        classified = recommend_cuts(
            llm,
            {"image_style": {}, "editing_style": {}},
            guide,
        )

        self.assertEqual(llm.calls, 2)
        self.assertEqual(classified["visual_recommendation"]["status"], "complete")
        self.assertEqual(classified["visual_recommendation"]["retry_count"], 1)
        self.assertEqual(
            classified["segments"][0]["asset_classification_source"],
            "generation_llm",
        )

    def test_stock_cut_requires_compact_english_search_query(self) -> None:
        llm = _RepairingStockQueryLlm()
        guide = {
            "title": "베트남 거리",
            "segments": [
                {
                    "index": 0,
                    "duration_sec": 4,
                    "narration": "한낮의 베트남 도심을 보여준다",
                    "cuts_suggested": 2,
                }
            ],
        }

        classified = recommend_cuts(
            llm,
            {"image_style": {}, "editing_style": {}},
            guide,
        )
        manifest = build_cut_manifest(classified)

        self.assertEqual(llm.calls, 2)
        self.assertEqual(
            classified["segments"][0]["stock_query_suggestions"][0],
            "Vietnam tropical city street",
        )
        self.assertEqual(
            manifest.cuts[0].stock_query,
            "Vietnam tropical city street",
        )
        errors = classified["visual_recommendation"]["errors"]
        self.assertTrue(any("한글이 포함됨" in item["message"] for item in errors))

    def test_asset_director_splits_large_storyboard_into_small_batches(self) -> None:
        llm = _BatchAssetDirectorLlm()
        guide = {
            "title": "현재 주제",
            "segments": [
                {
                    "index": index,
                    "duration_sec": 4,
                    "narration": f"현재 주제의 {index}번째 장면",
                    "cuts_suggested": 2,
                }
                for index in range(5)
            ],
        }

        classified = recommend_cuts(
            llm,
            {"image_style": {}, "editing_style": {}},
            guide,
        )

        self.assertEqual(len(llm.calls), 2)
        self.assertTrue(all(sum(cuts for _, cuts in call) <= 8 for call in llm.calls))
        self.assertEqual(classified["visual_recommendation"]["status"], "complete")
        self.assertEqual(classified["visual_recommendation"]["batch_count"], 2)

    def test_failed_asset_director_never_reuses_learned_video_subjects(self) -> None:
        llm = _FailingAssetDirectorLlm()
        guide = {
            "title": "특수분유 이야기",
            "hook": "만들수록 손해인 분유",
            "segments": [
                {
                    "index": 0,
                    "duration_sec": 4,
                    "narration": "매일유업이 희귀질환 아이들을 위한 특수분유를 만든다",
                    "cuts_suggested": 2,
                }
            ],
        }
        profile = {
            "image_style": {
                "subjects": [
                    "명랑핫도그 치즈가 늘어나는 사진",
                    "명랑핫도그 제품을 보여주는 사진",
                ]
            },
            "editing_style": {},
        }

        classified = recommend_cuts(llm, profile, guide)
        segment = classified["segments"][0]

        self.assertEqual(llm.calls, 2)
        self.assertEqual(classified["visual_recommendation"]["status"], "fallback")
        self.assertEqual(segment["asset_classification_source"], "heuristic_fallback")
        self.assertIn("매일유업", segment["image_suggestions"][0])
        self.assertNotIn("명랑핫도그", " ".join(segment["image_suggestions"]))
        self.assertIn("visual_recommendation_warning", segment)

    def test_generic_person_and_split_words_are_not_profile_leaks(self) -> None:
        terms = _profile_specific_terms(
            {
                "subjects": [
                    "여성이 음식을 반으로 자르는 사진",
                    "여성이 제품을 반으로 자르는 장면",
                ]
            }
        )

        self.assertNotIn("여성이", terms)
        self.assertNotIn("반으로", terms)

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
        self.assertIn('return cut.selected_candidate_id?"재생성":"비용 확인·생성"', page)
        self.assertIn('const action=cut.selected_candidate_id?"재생성":"생성"', page)
        self.assertIn('실제 유료 ${action}을 승인할까요?', page)
        self.assertIn('data-stock-search="', page)
        self.assertIn("/stock-search?", page)
        self.assertIn("/stock-select", page)
        self.assertIn('value=cut.stock_query||""', page)
        self.assertNotIn("cut.stock_query||cut.prompt", page)
        self.assertIn("추천 검색어가 없습니다", page)
        self.assertIn("가상 인물 고지", page)
        self.assertIn("CC BY 결과도 포함", page)
        self.assertIn("현재 선택은 해제됩니다", page)
        self.assertIn("el.value=cut.asset_type", page)
        self.assertIn("스톡 이미지를 선택하지 않은 컷이 있습니다", page)
        self.assertIn("이미지가 자동 삽입되지 않습니다", page)
        self.assertIn("선택하지 않은 상태로 내보내기를 계속", page)
        self.assertIn("<th>미리보기</th>", page)
        self.assertIn("/preview?v=", page)
        self.assertIn("AI 이미지", page)
        self.assertNotIn("AI 이미지 생성 · 권장", page)
        self.assertIn(
            'replace(/^생성 모델 판단\\s*[:：]\\s*/,"")',
            page,
        )
        self.assertIn("failure.reason", page)
        self.assertIn("failure.recommendation", page)
        self.assertIn("failure.technical_detail", page)
        self.assertIn("재시도 권장", page)
        self.assertIn("수동 처리 검토", page)
        self.assertIn("job.result?.failure", page)
        self.assertIn("function legacyFailure(job)", page)
        self.assertIn("function actionMarkup(cut)", page)
        self.assertIn("data-restore-kind", page)
        self.assertIn("AI 영상 다시 시도", page)
        self.assertIn("이전 생성 실패로 수동 처리 유형이 된 컷입니다", page)
        self.assertIn(
            'asset_type:restoreKind==="video"?"generated_video":"generated_image"',
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

    def test_manifest_uses_semantic_cut_change_ratios(self) -> None:
        guide = _guide()
        guide["segments"][0]["sentence_count"] = 1
        guide["segments"][0]["cut_change_ratios"] = [0.25]

        manifest = build_cut_manifest(guide)

        self.assertEqual(manifest.cuts[0].start_sec, 0.0)
        self.assertEqual(manifest.cuts[0].duration_sec, 1.0)
        self.assertEqual(manifest.cuts[1].start_sec, 1.0)
        self.assertEqual(manifest.cuts[1].duration_sec, 3.0)

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

    def test_blank_product_video_requires_reference_guided_manual_generation(
        self,
    ) -> None:
        guide = {
            "title": "fixture",
            "total_duration_sec": 3,
            "segments": [
                {
                    "index": 0,
                    "start_sec": 0,
                    "duration_sec": 3,
                    "narration": "도시락 컵라면이 러시아 열차에서 인기를 얻었다",
                    "caption": "도시락의 인기",
                    "cuts_suggested": 1,
                    "image_suggestions": [
                        "열차 테이블 위 무지 흰색 사각 컵라면, 로고 없음"
                    ],
                    "media_suggestions": ["영상/움짤"],
                    "asset_type_suggestions": ["generated_video"],
                    "fact_sensitive_suggestions": [False],
                    "asset_reason_suggestions": [
                        "실제 브랜드 외형은 쓰지 않음"
                    ],
                }
            ],
        }
        cut = build_cut_manifest(guide).cuts[0]

        self.assertEqual(cut.asset_type, AssetType.MANUAL_REQUIRED)
        self.assertEqual(cut.media_kind, MediaKind.VIDEO)
        self.assertTrue(cut.fact_sensitive)
        self.assertTrue(any("무브랜드" in note for note in cut.notes))

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
                with patch(
                    "shortly.apps.personal.automation.api.prepare_guide_tts_timing",
                    side_effect=lambda guide, *_args: (
                        guide,
                        {"status": "disabled"},
                    ),
                ), TestClient(
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
