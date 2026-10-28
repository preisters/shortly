"""TTS(음성 합성) 연동. 현재 Typecast(신규 API)를 지원합니다."""

from .typecast import (TTSError, TypecastConfig, normalize_wav_for_export,
                       synth_to_file, synth_wav, wav_duration_us)

__all__ = ["TTSError", "TypecastConfig", "synth_wav", "synth_to_file",
           "wav_duration_us", "normalize_wav_for_export"]
