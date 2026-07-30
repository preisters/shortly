from __future__ import annotations

import unittest

from support import load_fixture

from shortly.generate.guide import build_guide
from shortly.generate.script_gen import (
    extract_subject_anchors,
    generate_script,
    script_preserves_subject,
)
from shortly.llm.base import LLMClient


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


if __name__ == "__main__":
    unittest.main()
