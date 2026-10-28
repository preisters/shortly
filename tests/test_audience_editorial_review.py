"""Prompt wiring/feedback contracts, not proof of audience engagement quality."""
import copy
import json
import unittest

from shortly.apps.personal.automation.review import qwen_read_only_review
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest
from shortly.generate.editorial import WHOLE_VIDEO_REVIEW_GUIDANCE, review_story
from shortly.generate.image_reco import _recommend_llm
from shortly.generate.script_gen import _FLOW_FACT_REMINDER, _SYSTEM_PROMPT


class Capture:
    available = True
    text_model = "offline-fixture"

    def __init__(self, response=None):
        self.response = response or {}
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append((system, user))
        return copy.deepcopy(self.response)


class AudienceEditorialReviewTests(unittest.TestCase):
    def test_generation_and_repair_share_whole_video_rules(self):
        for prompt in (_SYSTEM_PROMPT, _FLOW_FACT_REMINDER):
            with self.subTest(prompt=prompt[:20]):
                self.assertIn(WHOLE_VIDEO_REVIEW_GUIDANCE, prompt)
                self.assertIn("같은 칭찬의 반복", prompt)
                self.assertIn("그 뒤에도 볼 구체적 이유", prompt)

    def test_rules_do_not_require_negativity_or_a_universal_structure(self):
        self.assertIn("사건 이해에 필요한 정보는 보존", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("갈등·위기·반전을 창작하지 않는다", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("타이틀 제거·결말 선공개·영상 단축을 보편 해법으로 강제하지", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("사용자 지정 범위를 지킨다", WHOLE_VIDEO_REVIEW_GUIDANCE)

    def test_joint_hook_and_fresh_rewards_reach_generation_and_repair(self):
        for prompt in (_SYSTEM_PROMPT, _FLOW_FACT_REMINDER):
            with self.subTest(prompt=prompt[:20]):
                self.assertIn("제목과 첫 이미지/영상은 한 쌍", prompt)
                self.assertIn("어떤 구체적인 질문을 만드는지", prompt)
                self.assertIn("새 명사·숫자·사진만 추가된 것을 새로운 재미로 세지", prompt)
                self.assertIn("제공된 화면 계획이 없으면 공동 훅은 미확인", prompt)
        self.assertIn("AI 비판이나 부정적인 사건에 한정하지", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("모든 컷에 반전·기괴한 이미지·떡밥을 강제하지", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("인과 효과를 확정하거나", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("가짜 사건 증거를 만들지", WHOLE_VIDEO_REVIEW_GUIDANCE)

    def test_flexible_priorities_and_preservation_reach_generation_and_repair(self):
        for prompt in (_SYSTEM_PROMPT, _FLOW_FACT_REMINDER):
            with self.subTest(prompt=prompt[:20]):
                self.assertIn("소재 자체의 궁금증, 확보 가능한 시각 자료", prompt)
                self.assertIn("평가 우선순위이지 정해진 순서·비트 수·길이·결말 위치가 아니다", prompt)
                self.assertIn("원리 설명·기원 추적·미담도 소재에 맞으면 유지", prompt)
                self.assertIn("'수정 불필요'도 정상적인 검수 결론", prompt)
                self.assertIn("기획 양식에 맞추려고 다시 쓰지 않는다", prompt)
                self.assertIn("근거가 약하면 원본을 유지하고 가설만 남긴다", prompt)
                self.assertIn("기존 프로젝트·요청 밖 구간·수동 편집의 변경 승인이 아니다", prompt)

    def test_existing_good_story_can_pass_without_a_rewrite_or_retry(self):
        response = {"dimensions": {key: {
            "passed": True,
            "reason": "기존 구절이 필요한 설명과 흐름을 갖추고 있어 수정 불필요",
        } for key in ("intent", "hook", "progression", "payoff", "economy")}}
        llm = Capture(response)
        script = {"title": "익숙한 음식의 뜻밖의 사연", "segments": [
            {"index": 0, "text": "궁금했던 이유를 설명하는 현재 문장"},
            {"index": 1, "text": "이해에 필요한 원리를 거쳐 답으로 이어지는 문장"},
        ]}
        before = copy.deepcopy(script)
        result = review_story(llm, script, "음식의 원리 설명", "기존 대본 검수만", "", {})
        self.assertTrue(result["passed"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(script, before)
        self.assertEqual(json.loads(llm.calls[0][1])["script"], before)
        self.assertIn("잃을 수 있는 재미·맥락", llm.calls[0][0])
        self.assertIn("명확성·정보량·효과의 증가를 재미의 증가와 동일시하지", llm.calls[0][0])

    def test_metrics_do_not_authorize_a_body_rewrite_or_fixed_performance_target(self):
        self.assertIn("초반 선택률과 본문 시청 비율은 별도로 진단", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("선택률이 약하고 본문 지표가 강하면 본문 보존을 우선", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("실제 유지율 곡선과 본문을 먼저 검토", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("타이틀·목소리·속도를 이번 원칙만으로 바꾸지", WHOLE_VIDEO_REVIEW_GUIDANCE)
        self.assertIn("고정 합격선·조회수 개선 보장을 주장하지", WHOLE_VIDEO_REVIEW_GUIDANCE)

    def test_joint_hook_feedback_is_read_only_and_does_not_trigger_another_call(self):
        response = {"dimensions": {key: {"passed": True, "reason": "제공된 구절 확인"}
                    for key in ("intent", "hook", "progression", "payoff", "economy")}}
        response["dimensions"]["hook"] = {
            "passed": False,
            "reason": "제목은 실패 사건을 약속하지만 첫 화면 계획은 제품 정면 사진뿐이라 함께 생기는 질문이 불명확함",
        }
        llm = Capture(response)
        script = {"title": "돌아온 간식", "segments": [{"index": 0, "text": "다시 판매하기 시작했음"}]}
        before = copy.deepcopy(script)
        result = review_story(llm, script, "재출시 사건", "기존 제목 유지", "", {})
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("첫 화면 계획", result["issues"][0])
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("제공된 화면 계획이 없으면 공동 훅은 미확인", llm.calls[0][0])
        self.assertEqual(script, before)

    def test_story_review_preserves_specific_progression_failure_without_extra_call(self):
        response = {"dimensions": {key: {"passed": True, "reason": "제공된 문구의 연결 확인"}
                    for key in ("intent", "hook", "progression", "payoff", "economy")}}
        response["dimensions"]["progression"] = {
            "passed": False,
            "reason": "기부를 밝힌 도입 뒤 고급 재료·저렴한 가격·판매 1위만 이어져 같은 칭찬을 반복함",
        }
        llm = Capture(response)
        script = {"title": "기부한 과자", "segments": [
            {"index": 0, "text": "과자 수익금을 기부했음"},
            {"index": 1, "text": "고급 재료인데 저렴하고 판매 1위임"},
            {"index": 2, "text": "병원비를 갚았음"},
        ]}
        before = copy.deepcopy(script)
        result = review_story(llm, script, "기부 이야기", "제목과 수동 편집 유지", "", {})
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("같은 칭찬", result["issues"][0])
        self.assertEqual(len(llm.calls), 1)
        self.assertIn(WHOLE_VIDEO_REVIEW_GUIDANCE, llm.calls[0][0])
        self.assertEqual(json.loads(llm.calls[0][1])["script"], before)
        self.assertEqual(script, before)

    def test_visual_recommendation_carries_full_story_but_does_not_expand_batch(self):
        llm = Capture()
        segments = [
            {"index": 0, "narration": "과자 수익금이 쉼터로 갔음", "cuts": 1},
            {"index": 1, "narration": "고급 재료에 저렴한 가격임", "cuts": 1},
            {"index": 2, "narration": "병원비를 갚을 수 있었음", "cuts": 1},
        ]
        _recommend_llm(llm, {}, {}, {"title": "기부한 과자", "segments": segments}, segments=[segments[1]])
        self.assertEqual(len(llm.calls), 1)
        prompt = llm.calls[0][1]
        self.assertIn(WHOLE_VIDEO_REVIEW_GUIDANCE, prompt)
        self.assertIn("제목과 첫 이미지/영상은 한 쌍", prompt)
        self.assertIn("asset_reason_suggestions에는 이 컷의 사건 전달 역할", prompt)
        self.assertIn("'수정 불필요'도 정상적인 검수 결론", prompt)
        self.assertIn("잃을 수 있는 재미·맥락", prompt)
        self.assertIn("가짜 현장이나 가짜 문서", prompt)
        context, requested = prompt.split("[세그먼트]", 1)
        self.assertIn("과자 수익금이 쉼터로 갔음", context)
        self.assertIn("병원비를 갚을 수 있었음", context)
        self.assertIn("- index 1: 컷", requested)
        self.assertNotIn("- index 0: 컷", requested)

    def test_cut_review_keeps_global_advertorial_feedback_and_manifest_unchanged(self):
        llm = Capture({"passed": False, "summary": "상품 추천으로 읽히는 구성", "findings": [{
            "severity": "warning", "code": "advertorial_impression", "cut_id": None,
            "message": "첫 PICK 화보 뒤 가격·인기 칭찬이 이어져 사건 소개보다 광고로 보일 위험",
        }]})
        manifest = build_cut_manifest({"title": "기부한 과자", "segments": [{
            "duration_sec": 3, "narration": "과자 수익금을 기부했음", "image_suggestions": ["PICK 제품 화보"],
        }]})
        before = manifest.to_dict()
        report = qwen_read_only_review(llm, manifest)
        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].code, "advertorial_impression")
        self.assertIsNone(report.findings[0].cut_id)
        self.assertEqual(manifest.to_dict(), before)
        self.assertEqual(len(llm.calls), 1)
        system, user = llm.calls[0]
        self.assertIn("have not inspected image pixels", system)
        self.assertIn(WHOLE_VIDEO_REVIEW_GUIDANCE, user)
        self.assertIn("새 명사·숫자·사진만 추가된 것을 새로운 재미로 세지", user)
        self.assertIn("missing_post_hook_reason", user)
        self.assertIn("story_detour", user)
        self.assertIn("단어가 등장했다는 이유만으로 자동 불합격시키지", user)
        self.assertIn("평가 우선순위이지 정해진 순서", user)
        self.assertIn("기획 양식에 맞추려고 다시 쓰지 않는다", user)


if __name__ == "__main__":
    unittest.main()
