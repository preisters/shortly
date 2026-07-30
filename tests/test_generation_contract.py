from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import load_fixture

from shortly.generate.guide import build_guide
from shortly.generate.script_gen import (
    apply_revision_scope,
    extract_subject_anchors,
    generate_script,
    script_preserves_subject,
)
from shortly.llm.base import LLMClient
from shortly.service import _revision_lineage_context


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


if __name__ == "__main__":
    unittest.main()
