"""yt-dlp 로 채널 쇼츠 목록을 가져오고 영상/자막/메타를 내려받습니다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from ..config import Settings, get_settings
from ..utils import log, read_json, slugify, write_json

Progress = Callable[[str, Optional[int], Optional[int]], None]


def _noop(msg: str, cur: Optional[int] = None, total: Optional[int] = None) -> None:
    log.info(msg)


@dataclass
class ChannelPaths:
    slug: str
    root: Path

    @property
    def channel_json(self) -> Path:
        return self.root / "channel.json"

    @property
    def videos_dir(self) -> Path:
        return self.root / "videos"

    @property
    def profile_json(self) -> Path:
        return self.root / "profile.json"

    @property
    def report_md(self) -> Path:
        return self.root / "report.md"

    def video_dir(self, video_id: str) -> Path:
        return self.videos_dir / video_id


def channel_paths(channel_input: str, settings: Optional[Settings] = None) -> ChannelPaths:
    settings = settings or get_settings()
    slug = slugify(_channel_key(channel_input))
    return ChannelPaths(slug=slug, root=settings.channels_dir / slug)


def _channel_key(channel_input: str) -> str:
    """채널 입력에서 캐시 키(핸들/ID)를 뽑습니다."""
    s = channel_input.strip().rstrip("/")
    if "youtube.com" in s or "youtu.be" in s:
        # .../@handle/... 또는 .../channel/UC... 추출
        parts = [p for p in s.split("/") if p]
        for p in parts:
            if p.startswith("@") or p.startswith("UC"):
                return p
        return parts[-1] if parts else s
    return s.lstrip("@") or s


def resolve_shorts_url(channel_input: str) -> str:
    """채널 입력을 쇼츠 탭 URL 로 변환합니다."""
    s = channel_input.strip().rstrip("/")
    if s.startswith("http"):
        if s.endswith("/shorts"):
            return s
        # /videos, /featured 등이 붙어 있으면 교체
        for suffix in ("/videos", "/featured", "/streams", "/playlists"):
            if s.endswith(suffix):
                return s[: -len(suffix)] + "/shorts"
        return s + "/shorts"
    handle = s if s.startswith("@") else "@" + s
    return f"https://www.youtube.com/{handle}/shorts"


def _get_ydl():
    try:
        import yt_dlp  # type: ignore
        return yt_dlp
    except ImportError:
        return None


def list_shorts(channel_input: str, max_count: int = 30, min_views: int = 0,
                pool_size: Optional[int] = None, sort_by_views: bool = True,
                progress: Progress = _noop) -> List[dict]:
    """쇼츠 탭에서 영상 목록(메타)을 가져옵니다.

    - min_views: 이 조회수 이상만 학습 대상으로 남깁니다.
    - sort_by_views: 기본 True (조회수 높은 순으로 우선 학습).
    - 정렬/필터를 위해 max_count 보다 넉넉한 pool 을 먼저 가져와 추린 뒤 상위 max_count 개를 반환.
    """
    yt_dlp = _get_ydl()
    url = resolve_shorts_url(channel_input)
    if yt_dlp is None:
        raise RuntimeError("yt-dlp 가 설치되어 있지 않습니다. `pip install yt-dlp`")

    if pool_size is None:
        # 조건에 맞는 후보를 충분히 확보하기 위해 넉넉히 조회(최대 300)
        pool_size = min(300, max(max_count * 4, 60)) if (sort_by_views or min_views > 0) else max_count

    progress(f"쇼츠 목록 조회(최대 {pool_size}개 후보): {url}", None, None)
    opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "playlistend": pool_size,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = (info or {}).get("entries") or []

    candidates: List[dict] = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id")
        if not vid:
            continue
        views = e.get("view_count")
        candidates.append({
            "id": vid,
            "title": e.get("title") or vid,
            "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
            "duration": e.get("duration"),
            "view_count": views if isinstance(views, int) else 0,
        })

    total_found = len(candidates)
    if min_views > 0:
        candidates = [c for c in candidates if c["view_count"] >= min_views]
    if sort_by_views:
        candidates.sort(key=lambda c: c["view_count"], reverse=True)

    shorts = candidates[:max_count]
    progress(
        f"후보 {total_found}개 중 조건 통과 {len(candidates)}개 → 상위 {len(shorts)}개 학습 대상",
        len(shorts), len(shorts),
    )
    return shorts


def download_short(video: dict, out_dir: Path, progress: Progress = _noop) -> dict:
    """단일 쇼츠의 영상/자막/메타를 내려받습니다. 이미 있으면 건너뜁니다."""
    yt_dlp = _get_ydl()
    if yt_dlp is None:
        raise RuntimeError("yt-dlp 가 설치되어 있지 않습니다.")
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = list(out_dir.glob("video.*"))
    video_files = [p for p in existing if p.suffix.lower() in {".mp4", ".mkv", ".webm"}]
    if video_files:
        progress(f"이미 존재: {video.get('title')}", None, None)
    else:
        opts = {
            "outtmpl": str(out_dir / "video.%(ext)s"),
            "format": "mp4/bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "writeinfojson": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["ko", "en", "ko-orig"],
            "subtitlesformat": "vtt",
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "retries": 3,
        }
        progress(f"다운로드: {video.get('title')}", None, None)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([video["url"]])
        video_files = [p for p in out_dir.glob("video.*")
                       if p.suffix.lower() in {".mp4", ".mkv", ".webm"}]

    info_files = list(out_dir.glob("*.info.json"))
    sub_files = [p for p in out_dir.glob("video*.vtt")]
    return {
        "id": video["id"],
        "title": video.get("title"),
        "view_count": video.get("view_count"),
        "dir": str(out_dir),
        "video_path": str(video_files[0]) if video_files else None,
        "info_path": str(info_files[0]) if info_files else None,
        "subtitle_paths": [str(p) for p in sub_files],
    }


def ingest_channel(channel_input: str, settings: Optional[Settings] = None,
                   max_count: Optional[int] = None, min_views: Optional[int] = None,
                   sort_by_views: bool = True, progress: Progress = _noop) -> dict:
    """채널 쇼츠를 목록화하고 다운로드해 매니페스트를 저장합니다."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    max_count = max_count or settings.max_shorts
    min_views = settings.min_views if min_views is None else min_views
    paths = channel_paths(channel_input, settings)
    paths.videos_dir.mkdir(parents=True, exist_ok=True)

    shorts = list_shorts(channel_input, max_count=max_count, min_views=min_views,
                         sort_by_views=sort_by_views, progress=progress)
    downloaded: List[dict] = []
    for i, v in enumerate(shorts, start=1):
        progress(f"[{i}/{len(shorts)}] {v.get('title')}", i, len(shorts))
        try:
            info = download_short(v, paths.video_dir(v["id"]), progress=progress)
            downloaded.append(info)
        except Exception as exc:  # noqa: BLE001
            log.warning("다운로드 실패 (%s): %s", v.get("id"), exc)

    manifest = {
        "input": channel_input,
        "slug": paths.slug,
        "shorts_url": resolve_shorts_url(channel_input),
        "count": len(downloaded),
        "criteria": {"max_count": max_count, "min_views": min_views,
                     "sort_by_views": sort_by_views},
        "videos": downloaded,
    }
    write_json(paths.channel_json, manifest)
    progress(f"수집 완료: {len(downloaded)}개", len(downloaded), len(downloaded))
    return manifest


def load_manifest(channel_input: str, settings: Optional[Settings] = None) -> Optional[dict]:
    paths = channel_paths(channel_input, settings)
    return read_json(paths.channel_json, default=None)
