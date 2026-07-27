"""효과음 라이브러리 폴더를 스캔해 카탈로그(메타데이터)를 만듭니다.

파일명/길이 기반 휴리스틱으로 sfx(짧은 효과음) / bgm(음악·긴 트랙) 을 구분합니다.
"""

from __future__ import annotations

import subprocess
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from ..config import Settings, get_settings
from ..utils import have_ffmpeg, log, read_json, write_json

AUDIO_EXTS = {".mp3", ".wav", ".wma", ".mp2", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

# 짧은 효과음으로 볼 최대 길이(초). 이보다 길면 bgm/music 으로 분류.
SFX_MAX_SEC = 8.0


@dataclass
class SfxItem:
    id: int
    name: str
    filename: str
    path: str
    ext: str
    duration_sec: float = 0.0
    kind: str = "sfx"  # sfx | bgm
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _probe_duration(path: Path) -> float:
    """ffprobe 로 길이를 구합니다. 실패 시 0.0."""
    from shutil import which

    if which("ffprobe") is None:
        return 0.0
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True,
                             encoding="utf-8", errors="ignore").stdout.strip()
        return round(float(out), 3)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0.0


def _guess_tags(name: str) -> List[str]:
    n = name.lower()
    tags: List[str] = []
    keywords = {
        "bgm": ["bgm", "brgm", "mr", "inst", "remix", "music", "테마", "브금"],
        "vocal": ["비명", "웃음", "울음", "voice", "목소리"],
        "impact": ["펀치", "타격", "폭발", "쨍그랑", "충돌", "impact"],
        "meme": ["밈", "meme", "noooo", "nope"],
    }
    for tag, keys in keywords.items():
        if any(k in n for k in keys):
            tags.append(tag)
    return tags


def build_catalog(settings: Optional[Settings] = None, probe: bool = True) -> List[SfxItem]:
    """효과음 폴더를 스캔해 카탈로그를 만들고 저장합니다."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    sfx_dir = settings.sfx_dir
    if not sfx_dir.is_dir():
        log.warning("효과음 폴더가 없습니다: %s", sfx_dir)
        return []

    files = sorted(
        (p for p in sfx_dir.iterdir()
         if p.is_file() and p.suffix.lower() in MEDIA_EXTS
         and not p.name.startswith("._") and not p.name.startswith(".")),
        key=lambda p: unicodedata.normalize("NFC", p.name),
    )
    if probe and not have_ffmpeg():
        log.warning("ffprobe/ffmpeg 미설치: 길이 측정 없이 파일명 기반으로만 분류합니다.")
        probe = False

    items: List[SfxItem] = []
    for idx, path in enumerate(files, start=1):
        name = unicodedata.normalize("NFC", path.stem)
        duration = _probe_duration(path) if probe else 0.0
        tags = _guess_tags(name)
        if duration and duration > SFX_MAX_SEC:
            kind = "bgm"
        elif "bgm" in tags:
            kind = "bgm"
        else:
            kind = "sfx"
        items.append(SfxItem(
            id=idx, name=name, filename=path.name, path=str(path),
            ext=path.suffix.lower(), duration_sec=duration, kind=kind, tags=tags,
        ))
        if idx % 100 == 0:
            log.info("카탈로그 진행: %d/%d", idx, len(files))

    out = settings.sfx_index_dir / "catalog.json"
    write_json(out, [it.to_dict() for it in items])
    log.info("효과음 카탈로그 저장: %s (%d개)", out, len(items))
    return items


def load_catalog(settings: Optional[Settings] = None) -> List[SfxItem]:
    settings = settings or get_settings()
    data = read_json(settings.sfx_index_dir / "catalog.json", default=[])
    return [SfxItem(**d) for d in data]
