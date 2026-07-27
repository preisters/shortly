from __future__ import annotations

import unittest

from support import load_fixture

from shortly.generate.guide import build_guide
from shortly.generate.script_gen import generate_script
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


if __name__ == "__main__":
    unittest.main()
