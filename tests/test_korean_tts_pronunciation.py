import unittest
from pathlib import Path
from shortly.generate.capcut_export import _tts_pronunciation_text, _title_for_tts, _tts_cache_path
from shortly.tts.pronunciation import korean_speech_text
from shortly.tts.typecast import TypecastConfig, _build_payload


class KoreanPronunciationTests(unittest.TestCase):
    def test_chicken_by_particle(self):
        for source, expected in {
            "닭": "닥", "닭에": "달게", "닭이": "달기", "닭을": "달글",
            "닭은": "달근", "닭으로": "달그로", "닭도": "닥또",
            "닭만": "당만", "닭과": "닥꽈", "닭고기": "닥꼬기",
        }.items():
            with self.subTest(source=source):
                self.assertEqual(korean_speech_text(source), expected)

    def test_different_clusters_and_verb_exception(self):
        self.assertEqual(korean_speech_text("값이 없는 흙을 읽고 삶은 얇은"),
                         "갑씨 엄는 흘글 일꼬 살믄 얄븐")
        self.assertEqual(korean_speech_text("밟고 넓고 많고 싫어 앉아"),
                         "밥꼬 널꼬 만코 시러 안자")

    def test_unknown_morphology_and_names_unchanged(self):
        text = "닭 앞 닭아저씨 넓죽하다 읽기값없는상표 밝기 돼지꿈"
        self.assertEqual(korean_speech_text(text), "닥 앞 닭아저씨 넓죽하다 읽기값없는상표 밝기 돼지꿈")

    def test_spoken_copy_only_and_payload(self):
        display = "바삭한 닭에 매콤달콤한 고추장 양념을 입히고, 백김치로 만든 코울슬로까지 빵 사이에 끼워 넣었음."
        spoken = _tts_pronunciation_text(display)
        self.assertEqual(spoken, display.replace("닭에", "달게"))
        self.assertIn("닭에", display)
        self.assertEqual(_build_payload(TypecastConfig(api_key="test"), spoken)["text"], spoken)

    def test_idempotence_and_existing_rules(self):
        source = "돼지바 3.99달러 닭에 값이 읽고"
        once = _tts_pronunciation_text(source)
        self.assertEqual(once, "되지바 삼쩜 구구 달러 달게 갑씨 일꼬")
        self.assertEqual(_tts_pronunciation_text(once), once)
        self.assertEqual(_tts_pronunciation_text(_title_for_tts("닭의 비밀")), "닭의 비밀?")

    def test_changed_pronunciation_invalidates_audio_cache(self):
        cfg = TypecastConfig(api_key="test")
        original = "바삭한 닭에 양념을 입히고"
        self.assertNotEqual(_tts_cache_path(cfg, Path("cache"), original),
                            _tts_cache_path(cfg, Path("cache"), _tts_pronunciation_text(original)))

    def test_reviewed_phrase_flows_in_speech_not_captions(self):
        display = "초콜릿 이름이 응원 한마디처럼 들렸던 거임."
        spoken = _tts_pronunciation_text(display)
        self.assertEqual(spoken, "초콜릿 이름이 응원 한마디처럼 들렸던거임.")
        self.assertIn("들렸던 거임", display)
        self.assertEqual(_tts_pronunciation_text(spoken), spoken)
        cfg = TypecastConfig(api_key="test")
        self.assertEqual(_build_payload(cfg, spoken)["text"], spoken)
        self.assertNotEqual(_tts_cache_path(cfg, Path("cache"), display),
                            _tts_cache_path(cfg, Path("cache"), spoken))

    def test_phrase_join_does_not_guess_other_spacing_or_cross_line_break(self):
        for text in ("들렸던 거", "들렸던 거임은", "안들렸던 거임",
                     "들렸던\n거임", "들렸던, 거임", "했던 거임"):
            with self.subTest(text=text):
                self.assertEqual(korean_speech_text(text), text)
        self.assertEqual(korean_speech_text("들렸던\t거임"), "들렸던거임")

    def test_reviewed_value_nouns_and_particles(self):
        for source, expected in {
            "원가": "원까", "원가를": "원까를", "원가가": "원까가",
            "원가는": "원까는", "원가로": "원까로", "원가의": "원까의",
            "원가보다": "원까보다", "정가에": "정까에", "영양가도": "영양까도",
        }.items():
            with self.subTest(source=source):
                self.assertEqual(korean_speech_text(source), expected)
        self.assertEqual(_tts_pronunciation_text("직접 만들어 원가를 낮추니"),
                         "직접 만들어 원까를 낮추니")

    def test_value_reading_only_changes_speech_copy_and_cache(self):
        display = "핫도그값은 그대로 두고 원가를 줄이려다"
        spoken = _tts_pronunciation_text(display)
        self.assertEqual(spoken, "핫도그값은 그대로 두고 원까를 줄이려다")
        self.assertIn("원가를", display)
        self.assertEqual(_tts_pronunciation_text(spoken), spoken)
        cfg = TypecastConfig(api_key="test")
        self.assertEqual(_build_payload(cfg, spoken)["text"], spoken)
        self.assertNotEqual(_tts_cache_path(cfg, Path("cache"), display),
                            _tts_cache_path(cfg, Path("cache"), spoken))

    def test_value_rules_do_not_rewrite_unreviewed_longer_tokens(self):
        display = "무언가 뭔가 학원가 원가량 원가족 원가계산 새로운상표"
        self.assertEqual(korean_speech_text(display), display)


if __name__ == "__main__":
    unittest.main()
