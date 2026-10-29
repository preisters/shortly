from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import load_fixture

from shortly.generate.guide import build_guide
from shortly.generate.script_gen import (
    analyze_required_content_coverage,
    analyze_narrative_coverage,
    _style_context,
    analyze_story_flow,
    apply_revision_scope,
    extract_subject_anchors,
    generate_script,
    polish_story_flow,
    script_preserves_subject,
)
from shortly.llm.base import LLMClient
from shortly.service import _finalize_story_quality, _revision_lineage_context


class _FlowPolishLlm(LLMClient):
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        return {
            "title": "일본 시장 통째로 버린 김",
            "hook": "일본 시장 통째로 버린 김",
            "segments": [
                {"index": 1, "text": "강한 훅", "intent": "hook"},
                {"index": 2, "text": "주인공 공개", "intent": "reveal"},
                {"index": 3, "text": "갈등 발생", "intent": "conflict"},
                {"index": 4, "text": "결정적인 선택", "intent": "choice"},
                {
                    "index": 5,
                    "text": "2024년 수출 비중은 1%에서 40%로 커졌고",
                    "intent": "cost",
                },
                {
                    "index": 6,
                    "text": "수출 2위와 국내 품절로 훅 회수",
                    "intent": "payoff",
                },
            ],
        }


class _NumericDriftFlowLlm(LLMClient):
    available = True

    def complete_json(self, *args, **kwargs):
        return {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {"index": 1, "text": "강한 훅", "intent": "hook"},
                {"index": 2, "text": "주인공 공개", "intent": "reveal"},
                {"index": 3, "text": "갈등 발생", "intent": "conflict"},
                {"index": 4, "text": "선택", "intent": "choice"},
                {"index": 5, "text": "대가", "intent": "cost"},
                {
                    "index": 6,
                    "text": "2024년 수출 비중이 50%까지 커졌다고",
                    "intent": "payoff",
                },
            ],
        }


class _AddedClaimFlowLlm(LLMClient):
    available = True

    def complete_json(self, *args, **kwargs):
        return {
            "title": "독도 때문에 일본 시장 포기한 회사",
            "hook": "독도 때문에 일본 시장 포기한 회사",
            "segments": [
                {"index": 1, "text": "강한 훅", "intent": "hook"},
                {"index": 2, "text": "주인공 공개", "intent": "reveal"},
                {"index": 3, "text": "일본 측 요구가 들어왔음", "intent": "conflict"},
                {"index": 4, "text": "회사는 요구를 거절했음", "intent": "choice"},
                {"index": 5, "text": "미국 수출길이 열렸음", "intent": "reward"},
                {
                    "index": 6,
                    "text": "대만과 홍콩에서도 팔리며 전 제품이 품절됐음",
                    "intent": "payoff",
                },
            ],
        }


class _AddedCausalFlowLlm(LLMClient):
    available = True

    def complete_json(self, *args, **kwargs):
        return {
            "title": "제사상까지 올라간 초코파이",
            "hook": "제사상까지 올라간 초코파이",
            "segments": [
                {"index": 1, "text": "베트남에서 초콜릿은 더위 때문에 외면받았음"},
                {"index": 2, "text": "초코파이가 현지 시장의 판을 뒤집었음"},
                {
                    "index": 3,
                    "text": "안 팔릴 이유였던 게 결국 제사상에 오른 이유가 된 셈임",
                },
            ],
        }


class GenerationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_fixture("profile.min.json")
        self.script = load_fixture("script.min.json")
        self.unavailable_llm = LLMClient()

    def test_topic_uses_deterministic_heuristic_when_llm_is_unavailable(self) -> None:
        script = generate_script(
            self.unavailable_llm,
            self.profile,
            "합성 테스트 주제",
            is_script=False,
        )

        self.assertEqual(script["_source"], "heuristic")
        self.assertEqual(script["title"], script["hook"])
        self.assertEqual(script["title"], "합성 테스트 주제")
        self.assertGreaterEqual(len(script["segments"]), 1)
        self.assertEqual(
            [segment["index"] for segment in script["segments"]],
            list(range(len(script["segments"]))),
        )
        self.assertTrue(all(segment.get("text") for segment in script["segments"]))

    def test_hook_examples_are_optional_and_semantic_fit_has_priority(self) -> None:
        context = _style_context(self.profile)

        self.assertIn("적용 의무 없음", context)
        self.assertIn("소재 적합성", context)
        self.assertIn("의미 적합성을 임팩트보다 먼저", context)
        self.assertIn("개인 증언에 귀속", context)
        self.assertIn("제작법 설명으로 분량을 채우지 말 것", context)
        self.assertIn("부정적인 결과가 겹치는 상황에만", context)

    def test_story_flow_flags_hapil_used_as_generic_surprise(self) -> None:
        script = {
            "segments": [
                {"text": "강한 훅"},
                {"text": "주인공을 공개했음"},
                {"text": "대표 재료가 하필 한국 초코파이였음"},
                {"text": "설 제사상에 과자탑으로 올라갔음"},
                {"text": "가족이 함께 나눠 먹었음"},
            ]
        }

        issues = analyze_story_flow(script)

        self.assertTrue(any("부정적 우연이 아닌데 '하필'" in x for x in issues))

    def test_story_flow_allows_hapil_for_unfortunate_coincidence(self) -> None:
        script = {
            "segments": [
                {"text": "강한 훅"},
                {"text": "광고 모델을 어렵게 뽑았음"},
                {"text": "그런데 하필 다음 날 논란이 터졌음"},
                {"text": "제품 이미지까지 타격을 입었음"},
                {"text": "결국 광고를 내리게 됐음"},
            ]
        }

        issues = analyze_story_flow(script)

        self.assertFalse(any("부정적 우연이 아닌데 '하필'" in x for x in issues))

    def test_story_flow_flags_corporate_marketing_meta_as_weak_payoff(self) -> None:
        script = {
            "title": "제사상까지 올라간 초코파이",
            "hook": "제사상까지 올라간 초코파이",
            "segments": [
                {"index": 1, "text": "베트남에서 초코파이가 인기를 끌었음"},
                {"index": 2, "text": "급기야 제사상에 오르는 모습까지 화제가 됐음"},
                {"index": 3, "text": "오리온은 그 장면을 마케팅으로 활용하고 있음"},
            ],
        }

        issues = analyze_story_flow(script)

        self.assertIn(
            "마지막 비트가 시청자 보상보다 기업의 후속 홍보 정보에 머묾",
            issues,
        )

    def test_quality_gate_rejects_heuristic_or_thin_research(self) -> None:
        script = {
            "title": "임실 치즈의 탄생 비화",
            "hook": "임실 치즈의 탄생 비화",
            "segments": [
                {"index": index, "text": text}
                for index, text in enumerate(
                    [
                        "가난한 농민을 돕기 시작했음",
                        "산양을 키웠음",
                        "유럽에서 기술을 배웠음",
                        "여러 번 실패했음",
                        "한국 치즈의 출발점을 만들었음",
                    ]
                )
            ],
            "_source": "heuristic",
        }

        _, quality = _finalize_story_quality(
            self.unavailable_llm,
            self.profile,
            script,
            {"results": [], "hard_facts": [], "thin": True},
            source_text="임실 치즈 탄생 비화",
        )

        self.assertFalse(quality["passed"])

    def test_script_input_preserves_sentence_order_without_llm(self) -> None:
        text = "첫 문장입니다. 두 번째 문장입니다! 마지막 문장입니다?"
        script = generate_script(
            self.unavailable_llm,
            self.profile,
            text,
            is_script=True,
        )
        self.assertEqual(
            [segment["text"] for segment in script["segments"]],
            ["첫 문장입니다.", "두 번째 문장입니다!", "마지막 문장입니다?"],
        )
        self.assertEqual(script["hook"], "첫 문장입니다.")

    def test_guide_contains_timed_caption_cut_tts_and_sfx_contracts(self) -> None:
        guide = build_guide(self.profile, self.script)

        self.assertEqual(guide["title"], self.script["title"])
        self.assertEqual(len(guide["segments"]), len(self.script["segments"]))
        self.assertGreater(guide["total_duration_sec"], 0)
        previous_end = 0.0
        for index, segment in enumerate(guide["segments"]):
            self.assertEqual(segment["index"], index)
            self.assertEqual(segment["start_sec"], previous_end)
            self.assertGreater(segment["end_sec"], segment["start_sec"])
            self.assertGreaterEqual(segment["cuts_suggested"], 1)
            self.assertIn("speed", segment["tts"])
            self.assertIn("emotion", segment["tts"])
            self.assertIsInstance(segment["sfx"], list)
            previous_end = segment["end_sec"]
        self.assertEqual(guide["total_duration_sec"], previous_end)

    def test_guide_uses_sentence_structure_before_learned_cut_budget(self) -> None:
        profile = {
            "stats": {
                "avg_shot_sec": 2.41,
                "avg_cuts_per_min": 23.52,
                "dialogue_stats": {
                    "chars_per_sec": {"avg": 8.3},
                },
            },
            "recipe": {},
        }
        lengths = [37, 36, 37, 36, 37, 36, 37, 36]
        script = {
            "title": "학습 속도 테스트",
            "hook": "학습 속도 테스트",
            "segments": [
                {"index": index, "text": "가" * length, "intent": "설명"}
                for index, length in enumerate(lengths)
            ],
        }

        guide = build_guide(profile, script)

        self.assertAlmostEqual(guide["total_duration_sec"], 35.2, delta=0.2)
        self.assertEqual(
            sum(segment["cuts_suggested"] for segment in guide["segments"]),
            8,
        )
        self.assertEqual(
            guide["pacing_calculation"]["speaking_rate_chars_per_sec"],
            8.3,
        )
        self.assertEqual(
            guide["pacing_calculation"]["basis"],
            "sentence_structure_then_profile_pacing",
        )
        self.assertEqual(guide["pacing_calculation"]["learned_cut_target"], 14)

    def test_guide_splits_long_sentence_to_keep_cuts_under_five_seconds(self) -> None:
        profile = {
            "stats": {
                "avg_shot_sec": 2.41,
                "avg_cuts_per_min": 40.0,
                "dialogue_stats": {"chars_per_sec": {"avg": 8.3}},
            },
            "recipe": {},
        }
        script = {
            "title": "한 문장 한 화면",
            "hook": "한 문장 한 화면",
            "segments": [
                {
                    "index": 0,
                    "text": ("긴문장을 " * 24).strip() + ".",
                    "intent": "설명",
                },
            ],
        }

        guide = build_guide(profile, script)

        segment = guide["segments"][0]
        self.assertEqual(segment["cuts_suggested"], 3)
        self.assertEqual(len(segment["cut_change_ratios"]), 2)
        self.assertTrue(
            all(
                "5초 상한" in reason
                for reason in segment["cut_change_reasons"]
            )
        )

    def test_guide_does_not_treat_decimal_point_as_sentence_end(self) -> None:
        profile = {
            "stats": {
                "avg_shot_sec": 2.41,
                "avg_cuts_per_min": 6.0,
                "dialogue_stats": {"chars_per_sec": {"avg": 8.3}},
            },
            "recipe": {},
        }
        script = {
            "title": "소수점 테스트",
            "hook": "소수점 테스트",
            "segments": [
                {
                    "index": 0,
                    "text": "가격은 3.99달러였고 반응은 좋았음.",
                    "intent": "설명",
                }
            ],
        }

        segment = build_guide(profile, script)["segments"][0]

        self.assertEqual(segment["sentence_count"], 1)

    def test_guide_allows_up_to_three_cuts_at_long_clause_boundaries(self) -> None:
        profile = {
            "stats": {
                "avg_shot_sec": 2.41,
                "avg_cuts_per_min": 40.0,
                "dialogue_stats": {"chars_per_sec": {"avg": 8.3}},
            },
            "recipe": {},
        }
        text = "가" * 24 + ", " + "나" * 24 + ", " + "다" * 24 + "."
        script = {
            "title": "세 절 문장",
            "hook": "세 절 문장",
            "segments": [{"index": 0, "text": text, "intent": "설명"}],
        }

        guide = build_guide(profile, script)
        segment = guide["segments"][0]

        self.assertEqual(segment["sentence_count"], 1)
        self.assertEqual(segment["cuts_suggested"], 3)
        self.assertEqual(len(segment["cut_change_ratios"]), 2)
        self.assertTrue(all("의미 절 경계" in reason for reason in segment["cut_change_reasons"]))

    def test_guide_preserves_sentence_end_as_visual_change_boundary(self) -> None:
        profile = {
            "stats": {
                "avg_shot_sec": 2.41,
                "avg_cuts_per_min": 6.0,
                "dialogue_stats": {"chars_per_sec": {"avg": 8.3}},
            },
            "recipe": {},
        }
        script = {
            "title": "두 문장",
            "hook": "두 문장",
            "segments": [
                {
                    "index": 0,
                    "text": "첫 번째 사실을 충분히 설명했음. 두 번째 결과를 이어서 설명했음.",
                    "intent": "설명",
                }
            ],
        }

        segment = build_guide(profile, script)["segments"][0]

        self.assertEqual(segment["sentence_count"], 2)
        self.assertEqual(segment["cuts_suggested"], 2)
        self.assertEqual(segment["cut_change_reasons"], ["문장 종결 뒤 전환"])

    def test_revision_subject_anchor_rejects_similar_company_substitution(
        self,
    ) -> None:
        original = {
            "title": "일본 시장 통째로 버린 김",
            "hook": "일본 시장 통째로 버린 김",
            "segments": [
                {
                    "index": 1,
                    "text": "그건 바로 지도표 성경김이라고",
                },
                {
                    "index": 2,
                    "text": "결국 성경김은 독도 표기를 고수했다고",
                },
            ],
        }
        anchors = extract_subject_anchors(
            original,
            "성경김이 독도 때문에 일본 시장을 포기한 이야기",
        )
        substituted = {
            "title": "일본 시장 통째로 버린 쌀과자",
            "hook": "일본 시장 통째로 버린 쌀과자",
            "segments": [
                {
                    "index": 1,
                    "text": "그건 바로 전남 장성의 유아용 쌀과자라고",
                }
            ],
        }

        self.assertIn("성경김", anchors)
        self.assertTrue(script_preserves_subject(original, anchors))
        self.assertFalse(script_preserves_subject(substituted, anchors))

    def test_explicit_main_company_outranks_previous_episode_reference(self) -> None:
        source = (
            "주제: 일본 수출을 포기한 유아용 쌀과자 회사 ‘올바름’. "
            "첫 대사에서는 성경김을 떠올린다고 말하되, "
            "본문 주인공은 끝까지 올바름으로 유지해줘."
        )

        anchors = extract_subject_anchors({}, source)

        self.assertEqual(anchors, ["올바름"])

    def test_closing_only_revision_preserves_title_and_previous_segments(
        self,
    ) -> None:
        original = {
            "title": "일본 시장 통째로 버린 김",
            "hook": "일본 시장 통째로 버린 김",
            "segments": [
                {"index": 1, "text": "첫 문장"},
                {"index": 2, "text": "기존 마무리"},
            ],
            "_source": "llm",
        }
        candidate = {
            "title": "LLM이 바꾼 제목",
            "hook": "LLM이 바꾼 제목",
            "segments": [
                {"index": 1, "text": "LLM이 바꾼 첫 문장"},
                {"index": 2, "text": "해외에서 잘 팔리는 마무리"},
            ],
            "_source": "llm",
        }

        revised = apply_revision_scope(
            original,
            candidate,
            "마무리만 해외에서 잘나가는 내용으로 변경해줘",
        )

        self.assertEqual(revised["title"], original["title"])
        self.assertEqual(revised["hook"], original["hook"])
        self.assertEqual(revised["segments"][0]["text"], "첫 문장")
        self.assertEqual(
            revised["segments"][1]["text"],
            "해외에서 잘 팔리는 마무리",
        )

    def test_closing_only_revision_can_split_overloaded_ending(self) -> None:
        original = {
            "title": "원래 제목",
            "hook": "원래 제목",
            "segments": [
                {"index": 1, "text": "훅"},
                {"index": 2, "text": "기존 마무리"},
            ],
        }
        candidate = {
            "title": "바뀐 제목",
            "hook": "바뀐 제목",
            "segments": [
                {"index": 1, "text": "다시 쓴 훅"},
                {"index": 2, "text": "해외 반응"},
                {"index": 3, "text": "수출 성장"},
                {"index": 4, "text": "품절로 마무리"},
            ],
        }

        revised = apply_revision_scope(
            original,
            candidate,
            "마무리만 더 매끄럽게 바꿔줘",
        )

        self.assertEqual(revised["title"], "원래 제목")
        self.assertEqual(
            [segment["text"] for segment in revised["segments"]],
            ["훅", "해외 반응", "수출 성장", "품절로 마무리"],
        )
        self.assertEqual(
            [segment["index"] for segment in revised["segments"]],
            [1, 2, 3, 4],
        )

    def test_last_two_beats_revision_preserves_all_earlier_beats(self) -> None:
        original = {
            "title": "고정 제목",
            "hook": "고정 제목",
            "segments": [
                {"index": index, "text": f"원본 {index}"}
                for index in range(1, 10)
            ],
        }
        candidate = {
            "title": "바뀌면 안 되는 제목",
            "hook": "바뀌면 안 되는 제목",
            "segments": [
                {"index": 1, "text": "LLM이 바꾼 앞부분"},
                {"index": 2, "text": "대만·홍콩 수출 보상"},
                {"index": 3, "text": "주문 폭발과 품절 보상"},
            ],
        }

        revised = apply_revision_scope(
            original,
            candidate,
            "마지막 두 비트만 수정해줘. 제목과 앞부분은 변경하지 마.",
        )

        self.assertEqual(revised["title"], "고정 제목")
        self.assertEqual(
            [segment["text"] for segment in revised["segments"]],
            [
                "원본 1",
                "원본 2",
                "원본 3",
                "원본 4",
                "원본 5",
                "원본 6",
                "원본 7",
                "대만·홍콩 수출 보상",
                "주문 폭발과 품절 보상",
            ],
        )
        self.assertEqual(
            [segment["index"] for segment in revised["segments"]],
            list(range(1, 10)),
        )

    def test_revision_lineage_recovers_root_topic_and_prior_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir)
            initial = out_root / "initial"
            first_revision = out_root / "first"
            latest = out_root / "latest"
            for path in (initial, first_revision, latest):
                path.mkdir()

            (initial / "request.json").write_text(
                json.dumps(
                    {
                        "input_type": "topic",
                        "input_text": "성경김의 일본 수출 이야기",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (initial / "research.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "title": "성경김 해외 수출",
                                "url": "https://example.test/overseas",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (first_revision / "request.json").write_text(
                json.dumps(
                    {
                        "input_type": "revision",
                        "input_text": "후반부 수정",
                        "source_guide_name": "initial",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (first_revision / "factcheck.json").write_text(
                json.dumps(
                    {
                        "extra_results": [
                            {
                                "title": "성경김 수출 비중",
                                "url": "https://example.test/share",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (latest / "request.json").write_text(
                json.dumps(
                    {
                        "input_type": "revision",
                        "input_text": "마무리만 수정",
                        "source_guide_name": "first",
                        "original_input_text": "직전 수정 요청",
                        "original_input_type": "revision",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            context = _revision_lineage_context(latest)

        self.assertEqual(
            context["root_input_text"],
            "성경김의 일본 수출 이야기",
        )
        self.assertEqual(context["root_input_type"], "topic")
        self.assertEqual(len(context["research_results"]), 2)

    def test_style_context_does_not_promote_noisy_run_on_rules(self) -> None:
        profile = {
            "stats": {},
            "style_summary": (
                "빠른 음식 쇼츠다. 연결어를 활용해 긴 호흡의 문장을 "
                "쉼표로 이어간다."
            ),
            "recipe": {
                "voice": (
                    "문장을 딱 끊지 않고 쉼표로 이어간다. "
                    "사실, 하지만 같은 연결어를 반복한다."
                ),
                "do": [
                    "사실, 하지만, 그건 바로 등 연결어로 문장 전환",
                    "강한 훅 사용",
                ],
                "avoid": ["문장을 딱 떨어지게 끝맺기"],
            },
        }

        context = _style_context(profile)

        self.assertNotIn("문장을 딱 끊지", context)
        self.assertNotIn(
            "사실, 하지만, 그건 바로 등 연결어로 문장 전환",
            context,
        )
        self.assertNotIn("문장을 딱 떨어지게 끝맺기", context)
        self.assertIn("같은 사실 반복", context)
        self.assertIn("정보 공개 순서", context)
        self.assertIn("역설만 다시 선언하는 징검다리 비트는 삭제", context)
        self.assertIn("확인된 생활 장면·사람의 반응·후속 결과", context)
        self.assertNotIn("훅→정체→상황→선택/대치→대가→반전/보상", context)

    def test_style_context_excludes_editing_rules_and_broken_samples(self) -> None:
        profile = {
            "stats": {},
            "style_summary": "화면 78%에 자막을 넣고 빠른 컷 편집을 한다.",
            "dialogue_samples": [
                "현재 큰일났다는 업체, 사실 가격이 계속 올랐지만,",
                "대놓고 털려버린 치킨이라 대부분 아쉬움만 남았다고 합니다",
                "가격을 올렸다가 결국 손님이 전부 떠났다고",
            ],
            "recipe": {
                "voice": "반말 나레이션으로 '~다고'를 쓴다.",
                "tone": "친근하지만 날카로운 평가 톤이다.",
                "do": ["화면 78% 이상 텍스트 오버레이 배치"],
            },
        }

        context = _style_context(profile)

        self.assertNotIn("화면 78%", context)
        self.assertNotIn("빠른 컷 편집", context)
        self.assertNotIn("계속 올랐지만,", context)
        self.assertNotIn("아쉬움만 남았다고 합니다", context)
        self.assertIn("결국 손님이 전부 떠났다고", context)
        self.assertIn("훅의 온도·반말 나레이션 말투·어미", context)

    def test_narrative_coverage_rejects_origin_story_that_drops_core_actions(self) -> None:
        source = (
            "임실 치즈의 탄생 비화. 가난한 농민을 돕기 위해 산양을 키우고 "
            "유럽에서 기술을 배워 국내 치즈 산업의 출발점을 만든 이야기"
        )
        flat_script = {
            "segments": [
                {"text": "벨기에 신부가 임실에 왔음"},
                {"text": "지금은 치즈테마파크와 체험장이 유명함"},
                {"text": "결국 한국 치즈의 시작점이 됐다고 함"},
            ]
        }
        story_script = {
            "segments": [
                {"text": "가난한 농민을 돕기 위해 산양을 키우기 시작했음"},
                {"text": "실패 끝에 유럽에서 치즈 기술을 배워왔음"},
                {"text": "그렇게 국내 치즈 산업의 출발점을 만들었음"},
            ]
        }

        issues = analyze_narrative_coverage(flat_script, source)

        self.assertTrue(any("핵심 행동이 빠짐" in issue for issue in issues))
        self.assertEqual(analyze_narrative_coverage(story_script, source), [])

    def test_origin_story_subject_anchor_keeps_compound_name(self) -> None:
        source = (
            "임실 치즈의 탄생 비화. 가난한 농민을 돕기 위해 산양을 키우고 "
            "유럽에서 기술을 배운 이야기"
        )

        self.assertEqual(extract_subject_anchors({}, source), ["임실 치즈"])

    def test_flow_analysis_flags_fragmented_modifier(self) -> None:
        script = {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "주인공 공개"},
                {
                    "index": 3,
                    "text": "회사 이름처럼 올바르게 만든, 믿고 먹는 과자였음",
                },
                {"index": 4, "text": "갈등 발생"},
                {"index": 5, "text": "결정적인 선택"},
            ],
        }

        issues = analyze_story_flow(script)

        self.assertTrue(any("분리된 관형어" in issue for issue in issues))

    def test_flow_analysis_flags_numeric_dump_in_ending(self) -> None:
        script = {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "주인공 공개"},
                {"index": 3, "text": "갈등 발생"},
                {"index": 4, "text": "결정적인 선택"},
                {"index": 5, "text": "선택의 대가"},
                {
                    "index": 6,
                    "text": (
                        "근데 2024년 보도 기준으로 수출 비중이 1%에서 "
                        "40%까지 커지고 수출 2위에 올랐고 국내 품절까지 났다고"
                    ),
                },
            ],
        }

        issues = analyze_story_flow(script)

        self.assertTrue(
            any("정보가 과도하게 몰림" in issue for issue in issues)
        )

    def test_flow_analysis_flags_editorial_source_note_in_spoken_copy(self) -> None:
        script = {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "주인공 공개"},
                {"index": 3, "text": "갈등 발생"},
                {"index": 4, "text": "결정적인 선택"},
                {"index": 5, "text": "선택의 대가"},
                {
                    "index": 6,
                    "text": "미주 시장 공략에 나섰다는 소식까지 나왔고(2024년 보도 기준)",
                },
            ],
        }

        issues = analyze_story_flow(script)

        self.assertTrue(
            any("출처 메모·면책 문구" in issue for issue in issues)
        )

    def test_flow_analysis_flags_unnecessary_comparison_year(self) -> None:
        script = {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "세 제품 공개"},
                {
                    "index": 3,
                    "text": "근데 2022년에 비교해 보니 두 제품은 같은 레시피였음",
                },
                {"index": 4, "text": "포장 방식의 차이"},
                {"index": 5, "text": "추억이라는 결말"},
            ],
        }

        issues = analyze_story_flow(script)

        self.assertTrue(any("불필요한 연도" in issue for issue in issues))

    def test_flow_analysis_does_not_flag_story_relevant_release_year(self) -> None:
        script = {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "1974년에 삼각 커피포리가 출시됐음"},
                {"index": 3, "text": "세 제품 공개"},
                {"index": 4, "text": "포장 방식의 차이"},
                {"index": 5, "text": "추억이라는 결말"},
            ],
        }

        issues = analyze_story_flow(script)

        self.assertFalse(any("불필요한 연도" in issue for issue in issues))

    def test_flow_analysis_flags_weak_trailing_verdict(self) -> None:
        script = {
            "title": "아무도 믿지 않는 커피우유의 진실",
            "hook": "아무도 믿지 않는 커피우유의 진실",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "세 제품 공개"},
                {"index": 3, "text": "같은 레시피라는 반전"},
                {"index": 4, "text": "맛의 차이를 만든 기억"},
                {
                    "index": 5,
                    "text": "성분표에 안 적힌 재료가 승부를 갈랐던 거지.",
                },
            ],
        }

        issues = analyze_story_flow(script)

        self.assertTrue(any("최종 판정" in issue for issue in issues))

    def test_flow_analysis_accepts_conclusive_final_verdict(self) -> None:
        script = {
            "title": "아무도 믿지 않는 커피우유의 진실",
            "hook": "아무도 믿지 않는 커피우유의 진실",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "세 제품 공개"},
                {"index": 3, "text": "같은 레시피라는 반전"},
                {"index": 4, "text": "맛의 차이를 만든 기억"},
                {
                    "index": 5,
                    "text": "결국 진짜 비밀 재료는 추억이었던 거임.",
                },
            ],
        }

        issues = analyze_story_flow(script)

        self.assertFalse(any("최종 판정" in issue for issue in issues))

    def test_flow_analysis_flags_reward_that_skips_the_decision(self) -> None:
        script = {
            "title": "독도 때문에 일본 시장 포기한 회사2",
            "hook": "독도 때문에 일본 시장 포기한 회사2",
            "segments": [
                {"index": 1, "text": "독도 때문에 일본을 포기한 회사가 더 있음"},
                {"index": 2, "text": "올바름이라는 쌀과자 회사였음"},
                {"index": 3, "text": "포장지에 독도를 넣었음"},
                {"index": 4, "text": "일본에서 독도를 빼자는 제안이 들어왔음"},
                {"index": 5, "text": "연 매출의 15퍼센트라 한참 고민했음"},
                {"index": 6, "text": "그 봉지 그대로 미국 수출길이 열렸음"},
                {"index": 7, "text": "주문이 수천 건으로 폭발해 전 제품이 품절됐음"},
            ],
        }

        issues = analyze_story_flow(script)

        self.assertIn("대치 뒤 주인공의 선택 없이 보상으로 건너뜀", issues)
        self.assertIn("마지막 비트가 결과 나열로 끝나 훅·선택 회수가 부족함", issues)

    def test_flow_analysis_accepts_choice_and_hook_callback_in_ending(self) -> None:
        script = {
            "title": "독도 때문에 일본 시장 포기한 회사2",
            "hook": "독도 때문에 일본 시장 포기한 회사2",
            "segments": [
                {"index": 1, "text": "독도 때문에 일본을 포기한 회사가 더 있음"},
                {"index": 2, "text": "올바름이라는 쌀과자 회사였음"},
                {"index": 3, "text": "포장지에 독도를 넣었음"},
                {"index": 4, "text": "일본에서 독도를 빼자는 제안이 들어왔음"},
                {"index": 5, "text": "결국 일본 수출을 포기하고 독도 포장을 지켰음"},
                {"index": 6, "text": "그 포장 그대로 다른 해외 수출길이 열렸음"},
                {
                    "index": 7,
                    "text": "주문까지 폭발해 일본 하나 포기하고 없어서 못 파는 과자가 된 거임",
                },
            ],
        }

        issues = analyze_story_flow(script)

        self.assertNotIn("대치 뒤 주인공의 선택 없이 보상으로 건너뜀", issues)
        self.assertNotIn("마지막 비트가 결과 나열로 끝나 훅·선택 회수가 부족함", issues)

    def test_flow_polish_runs_only_for_detected_structure_problem(self) -> None:
        llm = _FlowPolishLlm()
        script = {
            "title": "일본 시장 통째로 버린 김",
            "hook": "일본 시장 통째로 버린 김",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "주인공 공개"},
                {"index": 3, "text": "갈등 발생"},
                {"index": 4, "text": "결정적인 선택"},
                {"index": 5, "text": "선택의 대가"},
                {
                    "index": 6,
                    "text": (
                        "2024년 기준 수출 비중 1%에서 40%, 수출 2위, "
                        "국내 품절까지 한꺼번에 설명하는 마무리"
                    ),
                },
            ],
            "_source": "llm",
        }

        polished = polish_story_flow(llm, {}, script)

        self.assertEqual(llm.calls, 1)
        self.assertTrue(polished["_source"].endswith("+flow"))
        self.assertEqual(analyze_story_flow(polished), [])

    def test_flow_polish_can_force_language_review_after_factcheck(self) -> None:
        llm = _FlowPolishLlm()
        script = {
            "title": "일본 시장 통째로 버린 김",
            "hook": "일본 시장 통째로 버린 김",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "주인공 공개"},
                {"index": 3, "text": "갈등 발생"},
                {
                    "index": 4,
                    "text": "2024년 수출 비중은 1%에서 40%로 커졌다고",
                },
                {
                    "index": 5,
                    "text": "국내 품절과 수출 2위로 선택의 대가를 회수했다고",
                },
            ],
            "_source": "llm+factcheck",
        }

        polished = polish_story_flow(llm, {}, script, force=True)

        self.assertEqual(llm.calls, 1)
        self.assertTrue(polished["_source"].endswith("+flow"))
        self.assertIn(
            "팩트체크 수정 뒤 문법·문장 연결·낭독 호흡 재검토 필요",
            polished["_flow_issues_before"],
        )

    def test_flow_polish_rejects_changed_factchecked_numbers(self) -> None:
        script = {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "주인공 공개"},
                {"index": 3, "text": "갈등 발생"},
                {"index": 4, "text": "결정적인 선택"},
                {"index": 5, "text": "선택의 대가"},
                {
                    "index": 6,
                    "text": (
                        "2024년 기준 수출 비중 1%에서 40%, 수출 2위, "
                        "국내 품절까지 한꺼번에 설명하는 마무리"
                    ),
                },
            ],
            "_source": "llm+factcheck",
        }

        polished = polish_story_flow(
            _NumericDriftFlowLlm(),
            {},
            script,
        )

        self.assertEqual(polished["segments"], script["segments"])
        self.assertEqual(
            polished["_flow_rejected"],
            "numeric_claims_changed",
        )

    def test_flow_polish_rejects_new_claims_after_factcheck(self) -> None:
        script = {
            "title": "독도 때문에 일본 시장 포기한 회사",
            "hook": "독도 때문에 일본 시장 포기한 회사",
            "segments": [
                {"index": 1, "text": "강한 훅"},
                {"index": 2, "text": "주인공 공개"},
                {"index": 3, "text": "일본 측 요구가 들어왔음"},
                {"index": 4, "text": "회사는 요구를 거절했음"},
                {"index": 5, "text": "미국 수출길이 열렸음"},
                {"index": 6, "text": "주문이 크게 늘었다는 결과를 설명하는 긴 마무리였음"},
            ],
            "_source": "llm+factcheck",
        }

        polished = polish_story_flow(
            _AddedClaimFlowLlm(),
            {},
            script,
            force=True,
        )

        self.assertEqual(polished["segments"], script["segments"])
        self.assertEqual(polished["_flow_rejected"], "factual_claims_added")
        self.assertEqual(
            set(polished["_flow_added_claims"]),
            {"대만", "홍콩", "전 제품", "품절"},
        )

    def test_flow_polish_rejects_new_causal_bridge_after_factcheck(self) -> None:
        script = {
            "title": "제사상까지 올라간 초코파이",
            "hook": "제사상까지 올라간 초코파이",
            "segments": [
                {"index": 1, "text": "베트남에서 초콜릿은 더위 때문에 외면받았음"},
                {"index": 2, "text": "초코파이가 현지 시장의 판을 뒤집었음"},
                {"index": 3, "text": "국민 간식을 넘어 제사상까지 올라갔음"},
            ],
            "_source": "llm+factcheck",
        }

        polished = polish_story_flow(
            _AddedCausalFlowLlm(),
            {},
            script,
            force=True,
        )

        self.assertEqual(polished["segments"], script["segments"])
        self.assertEqual(polished["_flow_rejected"], "causal_claims_added")
        self.assertGreaterEqual(
            sum(polished["_flow_added_causal_markers"].values()),
            1,
        )

    def test_subject_anchor_extracts_branded_product_from_topic_notes(self) -> None:
        source = (
            "제목 방향: 한국보다 러시아에서 더 팔리는 초코파이\n"
            "한국에서 탄생한 오리온 초코파이가 러시아에서 더 많이 팔린다."
        )

        anchors = extract_subject_anchors({}, source)

        self.assertEqual(anchors[0], "오리온 초코파이")

    def test_required_content_missing_fails_quality_gate(self) -> None:
        required = "2024년 러시아 판매량 16억 개\n전 세계 판매량의 40%"
        script = {
            "title": "러시아 초코파이",
            "hook": "러시아 초코파이",
            "segments": [
                {"index": 1, "text": "러시아에서 베리잼 맛이 인기를 얻었음"},
            ],
            "_source": "llm",
        }
        research = {
            "results": [
                {
                    "title": "공식 판매량",
                    "snippet": "2024년 러시아 16억 개, 전 세계의 40%",
                    "source_tier": "authoritative",
                },
                {
                    "title": "현지화 자료",
                    "snippet": "베리잼 현지화",
                    "source_tier": "editorial",
                },
            ],
            "hard_facts": ["2024년 러시아에서 16억 개가 판매됐다."],
            "required_content_coverage": [
                {"item": "2024년 러시아 판매량 16억 개", "result_count": 1},
                {"item": "전 세계 판매량의 40%", "result_count": 1},
            ],
            "thin": False,
        }

        coverage = analyze_required_content_coverage(script, required, research)
        _, quality = _finalize_story_quality(
            self.unavailable_llm,
            self.profile,
            script,
            research,
            source_text="러시아 초코파이",
            required_content=required,
        )

        self.assertEqual(len(coverage["missing"]), 2)
        self.assertFalse(quality["passed"])
        self.assertEqual(len(quality["required_content"]["missing"]), 2)


if __name__ == "__main__":
    unittest.main()
