"""Typecast 신규 API(text-to-speech) 클라이언트.

- 엔드포인트: POST https://api.typecast.ai/v1/text-to-speech
- 인증 헤더: X-API-KEY
- 응답: WAV(16-bit PCM, mono, 44100Hz) 바이너리
- 'toneup'/'tonedown' 감정은 ssfm-v30 모델에서만 지원.

의존성 없이 표준 라이브러리(urllib)만 사용합니다.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..utils import log

_TTS_URL = "https://api.typecast.ai/v1/text-to-speech"
_MAX_TEXT = 2000  # API 제한(문자)

# 사용자 지정 기본값(캐릭터 '지훈', 속도 1.3, toneup 100%)
DEFAULT_VOICE_ID = "tc_61f0859907085fc68561c9a1"
DEFAULT_MODEL = "ssfm-v30"          # toneup 은 v30 전용
DEFAULT_LANGUAGE = "kor"
DEFAULT_EMOTION = "toneup"
DEFAULT_INTENSITY = 1.0             # 100%
DEFAULT_TEMPO = 1.3                 # 1.3배속


class TTSError(Exception):
    """TTS 합성 실패."""


@dataclass
class TypecastConfig:
    api_key: str
    voice_id: str = DEFAULT_VOICE_ID
    model: str = DEFAULT_MODEL
    language: str = DEFAULT_LANGUAGE
    emotion_preset: str = DEFAULT_EMOTION
    emotion_intensity: float = DEFAULT_INTENSITY
    tempo: float = DEFAULT_TEMPO
    volume: int = 100
    pitch: int = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


def _build_payload(cfg: TypecastConfig, text: str) -> dict:
    payload = {
        "voice_id": cfg.voice_id,
        "text": text,
        "model": cfg.model,
        "output": {
            "volume": int(cfg.volume),
            "audio_pitch": int(cfg.pitch),
            "audio_tempo": round(float(cfg.tempo), 3),
            "audio_format": "wav",
        },
    }
    if cfg.language:
        payload["language"] = cfg.language
    if cfg.emotion_preset:
        # ssfm-v30 은 emotion_type=preset 을 요구, v21 은 없이도 동작
        prompt = {"emotion_preset": cfg.emotion_preset,
                  "emotion_intensity": round(float(cfg.emotion_intensity), 3)}
        if str(cfg.model).lower().startswith("ssfm-v3"):
            prompt["emotion_type"] = "preset"
        payload["prompt"] = prompt
    return payload


def synth_wav(cfg: TypecastConfig, text: str, timeout: int = 90) -> bytes:
    """텍스트 -> WAV 바이트. 실패 시 TTSError."""
    if not cfg.enabled:
        raise TTSError("Typecast API 키가 없습니다(TYPECAST_API_KEY).")
    text = (text or "").strip()
    if not text:
        raise TTSError("합성할 텍스트가 비어 있습니다.")
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT]
    body = json.dumps(_build_payload(cfg, text)).encode("utf-8")
    req = urllib.request.Request(
        _TTS_URL, data=body, method="POST",
        headers={"X-API-KEY": cfg.api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:300]
        except Exception:  # noqa: BLE001 - 오류 메시지 추출은 실패해도 무방
            pass
        raise TTSError(f"Typecast HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TTSError(f"Typecast 연결 실패: {exc.reason}") from exc
    if not data or len(data) < 44:
        raise TTSError("Typecast 응답이 비었거나 WAV 가 아닙니다.")
    return data


def wav_duration_us(data: bytes) -> int:
    """WAV 바이트의 재생 길이(마이크로초)."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            frames = w.getnframes()
            rate = w.getframerate() or 44100
            return int(round(frames / float(rate) * 1_000_000))
    except (wave.Error, EOFError):
        # 헤더 파싱 실패 시 데이터 크기로 근사(16-bit mono 44100Hz 가정)
        return int(round(max(0, len(data) - 44) / (44100 * 2) * 1_000_000))


def synth_to_file(cfg: TypecastConfig, text: str, out_path: Path,
                  timeout: int = 90, reuse: bool = True) -> int:
    """텍스트 -> WAV 파일 저장. 재생 길이(us) 반환.

    reuse=True 이고 out_path 에 유효한 WAV 가 이미 있으면 API 를 호출하지 않고
    그 파일의 길이를 반환합니다(내용 기반 파일명과 함께 캐시로 활용).
    """
    out_path = Path(out_path)
    if reuse and out_path.is_file() and out_path.stat().st_size > 44:
        try:
            dur = wav_duration_us(out_path.read_bytes())
            if dur > 0:
                log.info("TTS 재사용: %s (%.2fs)", out_path.name, dur / 1e6)
                return dur
        except (OSError, wave.Error):
            pass
    data = synth_wav(cfg, text, timeout=timeout)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    dur = wav_duration_us(data)
    log.info("TTS 생성: %s (%.2fs) <- %r", out_path.name, dur / 1e6, text[:24])
    return dur
