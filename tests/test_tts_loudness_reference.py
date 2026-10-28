import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
import wave
from array import array
import math
from unittest.mock import patch

from shortly.tts.loudness import measure_loudness, normalize_reference_wav
from shortly.tts.typecast import normalize_wav_for_export, TypecastConfig

class DefaultTempoTests(unittest.TestCase):
    def test_config_and_settings_default_to_user_selected_speed(self):
        from shortly.config import Settings
        self.assertEqual(TypecastConfig(api_key='not-used').tempo, 1.2)
        self.assertEqual(Settings().tts_tempo, 1.2)
        with patch.dict('os.environ', {'SHORTLY_TTS_TEMPO': ''}):
            self.assertEqual(Settings.load().typecast_config().tempo, 1.2)

    def test_explicit_speed_override_is_respected(self):
        from shortly.config import Settings
        with patch.dict('os.environ', {'SHORTLY_TTS_TEMPO': '1.25'}):
            self.assertEqual(Settings.load().typecast_config().tempo, 1.25)

@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for LUFS measurement')
class ReferenceLoudnessTests(unittest.TestCase):
    def make(self,p,amplitude=2000):
        samples=array('h',(int(amplitude*math.sin(2*math.pi*440*i/44100)) for i in range(44100*3)))
        with wave.open(str(p),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(samples.tobytes())
    def test_default_reference_and_non_destructive_cache(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'voice.wav';self.make(p);before=p.read_bytes()
            result=normalize_wav_for_export(p);stats=measure_loudness(result)
            self.assertAlmostEqual(stats['input_i'],-16.56,delta=.3)
            self.assertLessEqual(stats['input_tp'],-2.3)
            self.assertEqual(p.read_bytes(),before)
            with wave.open(str(result)) as w:self.assertEqual(w.getnframes(),44100*3)
            stamp=result.stat().st_mtime_ns
            self.assertEqual(normalize_reference_wav(p),result)
            self.assertEqual(result.stat().st_mtime_ns,stamp)
            self.make(p,4000)
            self.assertNotEqual(normalize_reference_wav(p),result)
    def test_silence_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'silence.wav';self.make(p,0)
            with self.assertRaises(ValueError):normalize_reference_wav(p)
    def test_short_high_crest_clip_reaches_target_without_trimming(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'short.wav';self.make(p,2000)
            with wave.open(str(p)) as w:data=array('h');data.frombytes(w.readframes(44100))
            data[22050]=26000
            with wave.open(str(p),'wb')as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(data.tobytes())
            result=normalize_reference_wav(p);stats=measure_loudness(result)
            self.assertAlmostEqual(stats['input_i'],-16.56,delta=.3)
            self.assertLessEqual(stats['input_tp'],-2.3)
            with wave.open(str(result)) as w:self.assertEqual(w.getnframes(),44100)
    def test_default_tempo(self):
        self.assertEqual(TypecastConfig(api_key='not-used').tempo,1.2)

if __name__=='__main__':unittest.main()
