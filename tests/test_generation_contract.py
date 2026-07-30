from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import load_fixture

from shortly.generate.guide import build_guide
from shortly.generate.script_gen import (
    _style_context,
    analyze_story_flow,
    apply_revision_scope,
    extract_subject_anchors,
    generate_script,
    polish_story_flow,
    script_preserves_subject,
)
from shortly.llm.base import LLMClient
from shortly.service import _revision_lineage_context


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
                {"index": 5, "text": "선택의 대가", "intent": "cost"},
                {"index": 6, "text": "해외 성과로 훅 회수", "intent": "payoff"},
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
        self.assertGreaterEqual(len(script["segments"]), 1)
        self.assertEqual(
            [segment["index"] for segment in script["segments"]],
            list(range(len(script["segments"]))),
        )
        self.assertTrue(all(segment.get("text") for segment in script["segments"]))

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


if __name__ == "__main__":
    unittest.main()
