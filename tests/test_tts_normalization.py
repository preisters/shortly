from __future__ import annotations

from array import array
import math
import tempfile
import unittest
import wave
from pathlib import Path

from shortly.tts.typecast import normalize_wav_for_export


def _write_tone(path: Path, amplitude: int, *, seconds: float = 0.4) -> None:
    rate = 44100
    samples = array(
        "h",
        (
            int(amplitude * math.sin(2 * math.pi * 440 * index / rate))
            for index in range(int(rate * seconds))
        ),
    )
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())


def _rms_dbfs(path: Path) -> float:
    with wave.open(str(path), "rb") as reader:
        samples = array("h")
        samples.frombytes(reader.readframes(reader.getnframes()))
    rms = math.sqrt(sum(float(value) ** 2 for value in samples) / len(samples))
    return 20 * math.log10(rms / 32767)


class TtsNormalizationTests(unittest.TestCase):
    def test_export_normalization_matches_different_source_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quiet = root / "quiet.wav"
            loud = root / "loud.wav"
            _write_tone(quiet, 2500)
            _write_tone(loud, 10000)
            quiet_original = quiet.read_bytes()
            loud_original = loud.read_bytes()

            quiet_normalized = normalize_wav_for_export(quiet, target_dbfs=-22.0)
            loud_normalized = normalize_wav_for_export(loud, target_dbfs=-22.0)

            self.assertEqual(quiet.read_bytes(), quiet_original)
            self.assertEqual(loud.read_bytes(), loud_original)
            self.assertAlmostEqual(_rms_dbfs(quiet_normalized), -22.0, places=1)
            self.assertAlmostEqual(_rms_dbfs(loud_normalized), -22.0, places=1)
            self.assertIn("normalized", quiet_normalized.parts)

    def test_export_normalization_reuses_current_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "voice.wav"
            _write_tone(source, 4000)

            first = normalize_wav_for_export(source, target_dbfs=-22.0)
            first_mtime = first.stat().st_mtime_ns
            second = normalize_wav_for_export(source, target_dbfs=-22.0)

            self.assertEqual(second, first)
            self.assertEqual(second.stat().st_mtime_ns, first_mtime)


if __name__ == "__main__":
    unittest.main()
