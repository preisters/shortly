"""Title-only rising intonation: punctuation is supplied to TTS, not display text."""
import unittest

from shortly.generate.capcut_export import _title_for_tts


class TitleTTSQuestionTests(unittest.TestCase):
    def test_title_question_does_not_modify_display_string(self):
        display = '사라질 뻔한 성심당을 되살린 직원들'
        spoken = _title_for_tts(display)
        self.assertEqual(spoken, display + '?')
        self.assertFalse(display.endswith('?'))

    def test_repeated_processing_does_not_duplicate_question_mark(self):
        spoken = _title_for_tts('제목?!... ')
        self.assertEqual(spoken, '제목?')
        self.assertEqual(_title_for_tts(spoken), spoken)

    def test_declarative_display_title_still_uses_question_tts(self):
        # A declarative on-screen title is not an opt-out from rising title TTS.
        display = '알고보면 같은 종인 양배추와 브로콜리'
        self.assertEqual(_title_for_tts(display), display + '?')
        self.assertEqual(_title_for_tts(display + '.'), display + '?')
        self.assertFalse(display.endswith('?'))

    def test_blank_title_has_no_punctuation_only_tts(self):
        for text in ('', ' ', '?!...'):
            self.assertEqual(_title_for_tts(text), '')


if __name__ == '__main__':
    unittest.main()
