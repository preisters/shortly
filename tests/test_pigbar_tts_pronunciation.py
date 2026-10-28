import unittest

from shortly.generate.capcut_export import _title_for_tts, _tts_pronunciation_text


class PigbarPronunciationTests(unittest.TestCase):
    def test_product_names_only_change_in_spoken_copy(self):
        display = "돼지바 복돼지바 색색돼지바 돼지띠 돼지꿈"
        self.assertEqual(_tts_pronunciation_text(display), "되지바 복되지바 색색되지바 돼지띠 돼지꿈")
        self.assertEqual(display, "돼지바 복돼지바 색색돼지바 돼지띠 돼지꿈")

    def test_title_and_decimal_rules_still_apply(self):
        self.assertEqual(_tts_pronunciation_text(_title_for_tts("돼지바의 비밀")), "되지바의 비밀?")
        self.assertEqual(_tts_pronunciation_text("돼지바 3.99달러"), "되지바 삼쩜 구구 달러")
        self.assertEqual(_tts_pronunciation_text("되지바"), "되지바")
