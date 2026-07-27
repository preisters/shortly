"""공용 유틸: 파일명 정규화, JSON 입출력, ffmpeg 오디오 디코딩, 로깅."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("shortly")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


_SLUG_RE = re.compile(r"[^0-9A-Za-z가-힣_-]+")


def slugify(text: str, max_len: int = 60) -> str:
    """폴더/파일명으로 안전한 슬러그를 만듭니다(한글 보존)."""
    text = unicodedata.normalize("NFC", (text or "").strip())
    text = text.replace(" ", "_")
    text = _SLUG_RE.sub("", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    return (text or "untitled")[:max_len]


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def have_command(name: str) -> bool:
    return shutil.which(name) is not None


def format_ts(seconds: float) -> str:
    """초 -> M:SS.mmm 형식."""
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes}:{rem:06.3f}"


def decode_audio_mono(path: Path, sr: int = 11025) -> Optional["object"]:
    """ffmpeg 로 임의 오디오/영상을 mono float32 numpy 배열로 디코딩합니다.

    numpy 미설치 또는 ffmpeg 미설치 시 None 을 반환합니다.
    """
    try:
        import numpy as np
    except ImportError:
        log.warning("numpy 가 없어 오디오 디코딩을 건너뜁니다.")
        return None
    if not have_ffmpeg():
        log.warning("ffmpeg 가 PATH 에 없어 오디오 디코딩을 건너뜁니다.")
        return None
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-f", "f32le", "-ac", "1", "-ar", str(sr), "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("오디오 디코딩 실패 (%s): %s", path, exc)
        return None
    return np.frombuffer(proc.stdout, dtype="<f4").copy()


def try_import(module: str):
    """선택 의존성을 안전하게 임포트. 실패 시 None."""
    try:
        return __import__(module)
    except Exception:  # noqa: BLE001 - 다양한 임포트 에러 대응
        return None
