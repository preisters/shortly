"""Non-destructive LUFS matching for the user's supplied Shorts reference."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

REFERENCE_LUFS = -16.56
REFERENCE_TRUE_PEAK = -2.5  # Slight margin below the reference's -2.31 dBTP.


def _ffmpeg(args: list[str]) -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("음량 보정에 FFmpeg가 필요합니다. 설치/실행 경로를 확인하세요.")
    result = subprocess.run([binary, "-hide_banner", "-nostats", *args],
                            capture_output=True, text=True, encoding="utf8",
                            errors="replace", timeout=120)
    if result.returncode:
        raise RuntimeError("FFmpeg 음량 분석/보정 실패: " + result.stderr[-1500:])
    return result.stderr


def measure_loudness(source: Path) -> dict[str, float]:
    result = _ffmpeg(["-i", str(source), "-vn", "-af",
                      "loudnorm=I=-16:TP=-2.5:LRA=11:print_format=json",
                      "-f", "null", "-"])
    blocks = re.findall(r'\{\s*"input_i".*?\}', result, re.S)
    if not blocks:
        raise RuntimeError("음량 측정 결과를 읽지 못했습니다.")
    data = json.loads(blocks[-1])
    values = {key: float(data[key]) for key in
              ("input_i", "input_tp", "input_lra", "input_thresh")}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("음성이 없거나 측정할 수 없는 무음 파일입니다.")
    return values


def normalize_reference_wav(source: Path, *, target_lufs: float = REFERENCE_LUFS,
                            true_peak: float = REFERENCE_TRUE_PEAK) -> Path:
    """Match perceived level, preserving PCM channels, sample rate and duration.

    Measure the final mix separately: a mono clip and a stereo render need not
    have identical playback gain. No dual-mono assumption is forced here.
    """
    source = Path(source)
    if not -70 <= target_lufs <= -5 or not -9 <= true_peak <= 0:
        raise ValueError("음량 목표 범위가 잘못되었습니다.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    root = source.parent / "normalized"
    root.mkdir(exist_ok=True)
    output = root / f"{source.stem}.lufs-v2-{target_lufs:g}-tp{true_peak:g}-{digest}.wav"
    if output.is_file() and output.stat().st_size > 44:
        with wave.open(str(output)) as cached:
            if cached.getnframes() > 0:
                return output
    with wave.open(str(source)) as original:
        rate, channels, frames = original.getframerate(), original.getnchannels(), original.getnframes()
    measured = measure_loudness(source)
    # loudnorm may use peak-limited linear mode for short (<3 s) title clips,
    # leaving them quieter than the target. Apply measured gain + an oversampled
    # look-ahead limiter instead; compensate its latency and verify the result.
    gain = target_lufs - measured['input_i']
    limit = 10 ** (true_peak / 20)
    fd, temporary = tempfile.mkstemp(prefix="loudness-", suffix=".wav", dir=root)
    os.close(fd)
    try:
        for attempt in range(3):
            if abs(gain) > 24:
                raise ValueError("과도한 증폭이 필요합니다. 음성 원본을 확인하세요.")
            chain = (f"aresample=192000,volume={gain}dB,"
                     f"alimiter=limit={limit}:level=false:attack=5:release=50:latency=true,"
                     f"aresample={rate},apad=whole_len={frames},atrim=end_sample={frames}")
            _ffmpeg(["-y", "-i", str(source), "-vn", "-af", chain,
                     "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le", temporary])
            after = measure_loudness(Path(temporary))
            error = target_lufs - after['input_i']
            if abs(error) <= .15:
                break
            gain += error
        with wave.open(temporary) as check:
            if (check.getnframes(), check.getframerate(), check.getnchannels()) != (frames, rate, channels):
                raise RuntimeError("음량 보정 중 음성 길이/채널이 변경되었습니다.")
        if abs(after['input_i'] - target_lufs) > .6 or after['input_tp'] > true_peak + .2:
            raise RuntimeError(f"음량 보정 결과가 목표 범위를 벗어났습니다: {after}. 원본을 유지합니다.")
        os.replace(temporary, output)
        output.with_suffix('.json').write_text(json.dumps(dict(
            source_sha256=digest, before=measured, after=after,
            target_lufs=target_lufs, true_peak=true_peak), indent=2), encoding='utf8')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output
