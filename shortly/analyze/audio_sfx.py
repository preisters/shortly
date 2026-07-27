"""영상 오디오에서 효과음을 drums 스템 + CLAP 으로 매칭하고, 대사·컷 맥락을 붙입니다."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..config import Settings, get_settings
from ..sfx.clap_match import ClapSfxIndex, match_video_sfx
from ..utils import have_ffmpeg, log

_clap_index: Optional[ClapSfxIndex] = None


def _get_clap_index(settings: Settings) -> Optional[ClapSfxIndex]:
    global _clap_index
    if _clap_index is None:
        _clap_index = ClapSfxIndex.load_or_build(settings)
    return _clap_index


def _nearest(times: List[float], t: float) -> Optional[float]:
    if not times:
        return None
    return min(times, key=lambda x: abs(x - t))


def _context_for_time(t: float, transcript: Dict, cv_result: Dict) -> Dict:
    """효과음 등장 시각의 대사/컷 맥락."""
    cue_text = ""
    for c in transcript.get("cues", []):
        if c["start"] - 0.6 <= t <= c["end"] + 0.6:
            cue_text = c["text"]
            break
    if not cue_text:
        prior = [c for c in transcript.get("cues", []) if c["start"] <= t]
        if prior:
            cue_text = prior[-1]["text"]
    cut_times = [tr["time"] for tr in cv_result.get("transitions", [])]
    near_cut = _nearest(cut_times, t)
    return {
        "dialogue_near": cue_text,
        "near_cut_sec": round(near_cut, 2) if near_cut is not None else None,
        "on_cut": near_cut is not None and abs(near_cut - t) <= 0.4,
    }


def analyze_audio_sfx(
    video_path: Path,
    transcript: Dict,
    cv_result: Dict,
    settings: Optional[Settings] = None,
) -> Dict:
    """drums 스템 + CLAP 으로 효과음 매칭 결과를 반환합니다."""
    settings = settings or get_settings()
    if not have_ffmpeg():
        return {"available": False, "reason": "ffmpeg 미설치"}

    index = _get_clap_index(settings)
    if index is None:
        return {"available": False, "reason": "CLAP 효과음 인덱스 없음(카탈로그/의존성 확인)"}

    try:
        impacts, raw_matches = match_video_sfx(video_path, index)
    except RuntimeError as exc:
        log.warning("효과음 매칭 실패 (%s): %s", video_path.name, exc)
        return {"available": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.warning("효과음 매칭 오류 (%s): %s", video_path.name, exc)
        return {"available": False, "reason": f"매칭 오류: {exc}"}

    sfx_usages: List[Dict] = []
    for m in raw_matches:
        ctx = _context_for_time(m["time_sec"], transcript, cv_result)
        item = {k: v for k, v in m.items() if k != "top3"}
        item["context"] = ctx
        sfx_usages.append(item)

    return {
        "available": True,
        "method": "drums_clap",
        "impacts_detected": len(impacts),
        "sfx_count": len(sfx_usages),
        "sfx_usages": sfx_usages,
        "bgm_usages": [],
    }
