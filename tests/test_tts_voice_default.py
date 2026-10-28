"""Kim Geon must remain consistent across future default synthesis paths."""
import os
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from shortly.config import Settings
from shortly.tts.typecast import DEFAULT_VOICE_ID, TypecastConfig, _build_payload
from shortly.generate.capcut_export import _tts_cache_path


class VoiceDefaultTests(unittest.TestCase):
    def test_dataclass_and_client_defaults_use_kim_geon(self):
        expected = 'tc_61c2f7741330d213c238cba6'
        self.assertEqual(Settings().tts_voice_id, expected)
        self.assertEqual(DEFAULT_VOICE_ID, expected)
        self.assertEqual(TypecastConfig(api_key='test').voice_id, expected)

    def test_environment_fallback_is_the_same_voice(self):
        with patch.dict(os.environ, {'SHORTLY_TTS_VOICE_ID': ''}):
            self.assertEqual(Settings.load().tts_voice_id, DEFAULT_VOICE_ID)

    def test_explicit_override_is_not_silently_replaced(self):
        with patch.dict(os.environ, {'SHORTLY_TTS_VOICE_ID': 'explicit-voice'}):
            self.assertEqual(Settings.load().tts_voice_id, 'explicit-voice')

    def test_default_payload_keeps_voice_tone_and_speed(self):
        payload = _build_payload(TypecastConfig(api_key='test'), '제목?')
        self.assertEqual(payload['voice_id'], DEFAULT_VOICE_ID)
        self.assertEqual(payload['prompt']['emotion_preset'], 'toneup')
        self.assertEqual(payload['output']['audio_tempo'], 1.2)

    def test_changing_voice_cannot_reuse_old_speech_cache(self):
        current = TypecastConfig(api_key='test')
        old = replace(current, voice_id='tc_61f0859907085fc68561c9a1')
        self.assertNotEqual(_tts_cache_path(current, Path('.'), '같은 대사'),
                            _tts_cache_path(old, Path('.'), '같은 대사'))

    def test_changing_speed_cannot_reuse_old_speech_cache(self):
        current = TypecastConfig(api_key='test')
        old = replace(current, tempo=1.1)
        self.assertNotEqual(_tts_cache_path(current, Path('.'), '같은 대사'),
                            _tts_cache_path(old, Path('.'), '같은 대사'))


if __name__ == '__main__':
    unittest.main()
