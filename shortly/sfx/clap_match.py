"""demucs drums 스템 + CLAP 임베딩으로 효과음을 매칭합니다.

보컬·BGM이 섞인 원본 믹스에서는 지문 매칭이 실패하므로,
demucs가 분리한 drums 스템(짧은 타격/임팩트)만 CLAP 유사도로 라이브러리와 대조합니다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..config import Settings, get_settings
from ..utils import decode_audio_mono, log, read_json
from .catalog import SfxItem, load_catalog

CLAP_SR = 48_000
CLAP_MODEL_ID = "laion/clap-htsat-unfused"

# demucs drums 스템 잔향과 혼동되기 쉬운 라이브러리 항목
SFX_BLACKLIST = frozenset({
    "드럼",
    "바람일으킴",
    "파우더 두드리는 북소리",
})

# 기본 파라미터(조사에서 검증된 값)
DEFAULT_IMPACT_K = 8.0
DEFAULT_IMPACT_GAP = 0.4
DEFAULT_IMPACT_WIN = 0.35
DEFAULT_MIN_SIM = 0.45
DEFAULT_MAX_IMPACTS = 40  # 초과 시 임계값을 높여 demucs 아티팩트 억제


_demucs_model = None
_demucs_device: Optional[str] = None
_clap_processor = None
_clap_model = None
_clap_device: Optional[str] = None


def _clap_cache_paths(settings: Settings) -> Tuple[Path, Path]:
    d = settings.sfx_index_dir
    return d / "clap_embeds.npz", d / "clap_embeds_meta.json"


def _catalog_fingerprint(settings: Settings) -> dict:
    cat_path = settings.sfx_index_dir / "catalog.json"
    if not cat_path.is_file():
        return {"count": 0, "mtime": 0.0}
    st = cat_path.stat()
    items = read_json(cat_path, default=[])
    sfx_n = sum(1 for it in items if it.get("kind") == "sfx")
    return {"count": sfx_n, "mtime": st.st_mtime}


def _get_demucs():
    global _demucs_model, _demucs_device
    if _demucs_model is not None:
        return _demucs_model, _demucs_device
    try:
        import torch
        from demucs.pretrained import get_model
    except ImportError as exc:
        raise RuntimeError("demucs 미설치") from exc
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("demucs 모델 로드 중...")
    _demucs_model = get_model("htdemucs").to(device).eval()
    _demucs_device = device
    return _demucs_model, device


def _get_clap():
    global _clap_processor, _clap_model, _clap_device
    if _clap_model is not None:
        return _clap_processor, _clap_model, _clap_device
    try:
        import torch
        from transformers import ClapModel, ClapProcessor
    except ImportError as exc:
        raise RuntimeError("transformers 미설치") from exc
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("CLAP 모델 로드 중...")
    _clap_processor = ClapProcessor.from_pretrained(CLAP_MODEL_ID)
    _clap_model = ClapModel.from_pretrained(CLAP_MODEL_ID).to(device).eval()
    _clap_device = device
    return _clap_processor, _clap_model, device


def _embed_audio(processor, model, device: str, samples: np.ndarray) -> np.ndarray:
    inputs = processor(audio=[samples], sampling_rate=CLAP_SR, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    import torch
    with torch.no_grad():
        out = model.get_audio_features(**inputs)
    return out.pooler_output.cpu().numpy()[0]


def extract_drums_stem(video_path: Path) -> Tuple[np.ndarray, int]:
    """영상에서 demucs drums 모노 스템을 추출합니다."""
    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile

    model, device = _get_demucs()
    wav = AudioFile(video_path).read(
        streams=0, samplerate=model.samplerate, channels=model.audio_channels,
    )
    ref = wav.mean(0)
    wav_n = (wav - ref.mean()) / ref.std()
    with torch.no_grad():
        sources = apply_model(model, wav_n[None].to(device), device=device, progress=False)[0]
    sources = sources * ref.std() + ref.mean()
    drums_idx = model.sources.index("drums")
    drums = sources[drums_idx].mean(0).cpu().numpy()
    return drums, model.samplerate


def detect_drums_impacts(
    drums: np.ndarray,
    sr: int,
    k: float = DEFAULT_IMPACT_K,
    gap_sec: float = DEFAULT_IMPACT_GAP,
) -> List[float]:
    """drums 스템 RMS 피크로 임팩트 시각을 찾습니다."""
    win = max(1, int(0.05 * sr))
    rms = np.sqrt(np.convolve(drums ** 2, np.ones(win) / win, mode="same"))
    med = float(np.median(rms))
    if med < 1e-9:
        return []
    thresh = med * k
    min_gap = int(gap_sec * sr)
    n = len(rms)
    peaks: List[float] = []
    i = 0
    while i < n:
        if rms[i] > thresh:
            end = min(n, i + min_gap)
            idx = i + int(np.argmax(rms[i:end]))
            peaks.append(idx / sr)
            i = idx + min_gap
        else:
            i += 1
    if len(peaks) > DEFAULT_MAX_IMPACTS and k < 14.0:
        return detect_drums_impacts(drums, sr, k=k * 1.5, gap_sec=gap_sec)
    return peaks


class ClapSfxIndex:
    """효과음 라이브러리 CLAP 임베딩 인덱스(디스크 캐시)."""

    def __init__(self, embeds: np.ndarray, meta: List[dict]) -> None:
        self.embeds = embeds
        self.meta = meta
        self._name_to_id = {m["name"]: m["id"] for m in meta}

    @classmethod
    def load_or_build(cls, settings: Optional[Settings] = None) -> Optional["ClapSfxIndex"]:
        settings = settings or get_settings()
        settings.ensure_dirs()
        npz_path, meta_path = _clap_cache_paths(settings)
        fp = _catalog_fingerprint(settings)

        if npz_path.is_file() and meta_path.is_file():
            cached = read_json(meta_path, default={})
            if cached.get("catalog_count") == fp["count"] and cached.get("catalog_mtime") == fp["mtime"]:
                data = np.load(npz_path)
                meta = read_json(meta_path, default={}).get("items", [])
                if len(meta) > 0 and data["embeds"].shape[0] == len(meta):
                    log.info("CLAP 임베딩 캐시 로드: %d개", len(meta))
                    return cls(data["embeds"], meta)

        items_raw = load_catalog(settings)
        if not items_raw:
            log.warning("효과음 카탈로그 없음 — catalog.json 을 먼저 구축하세요.")
            return None
        sfx_items = [it for it in items_raw if it.kind == "sfx"]
        if not sfx_items:
            return None

        try:
            processor, model, device = _get_clap()
        except RuntimeError as exc:
            log.warning("CLAP 로드 실패: %s", exc)
            return None

        t0 = time.time()
        embeds, meta = [], []
        for it in sfx_items:
            s = decode_audio_mono(Path(it.path), sr=CLAP_SR)
            if s is None or len(s) < 480:
                continue
            if len(s) > CLAP_SR * 10:
                s = s[: CLAP_SR * 10]
            embeds.append(_embed_audio(processor, model, device, s))
            meta.append({"id": it.id, "name": it.name, "path": it.path})
        if not embeds:
            return None
        embeds_arr = np.stack(embeds)
        np.savez_compressed(npz_path, embeds=embeds_arr)
        meta_path.write_text(json.dumps({
            "catalog_count": fp["count"],
            "catalog_mtime": fp["mtime"],
            "items": meta,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("CLAP 임베딩 구축 완료: %d개, %.1fs", len(meta), time.time() - t0)
        return cls(embeds_arr, meta)

    def query_clip(
        self,
        clip: np.ndarray,
        processor,
        model,
        device: str,
        min_sim: float = DEFAULT_MIN_SIM,
        top_k: int = 3,
    ) -> List[Tuple[dict, float]]:
        if len(clip) < 480:
            return []
        q = _embed_audio(processor, model, device, clip)
        sims = self.embeds @ q
        order = np.argsort(-sims)
        out: List[Tuple[dict, float]] = []
        for i in order[:top_k]:
            sim = float(sims[i])
            if sim < min_sim:
                break
            name = self.meta[i]["name"]
            if name in SFX_BLACKLIST:
                continue
            out.append((self.meta[i], sim))
        return out


def match_video_sfx(
    video_path: Path,
    index: ClapSfxIndex,
    min_sim: float = DEFAULT_MIN_SIM,
    impact_win: float = DEFAULT_IMPACT_WIN,
) -> Tuple[List[float], List[dict]]:
    """단일 영상의 drums 임팩트를 라이브러리와 매칭합니다."""
    import torch
    import torchaudio.functional as F

    drums, sr = extract_drums_stem(video_path)
    impacts = detect_drums_impacts(drums, sr)
    if not impacts:
        return [], []

    processor, model, device = _get_clap()
    drums_48k = F.resample(torch.from_numpy(drums).float(), sr, CLAP_SR).numpy()

    matches: List[dict] = []
    for t in impacts:
        lo = max(0, int((t - impact_win) * CLAP_SR))
        hi = min(len(drums_48k), int((t + impact_win) * CLAP_SR))
        clip = drums_48k[lo:hi]
        cands = index.query_clip(clip, processor, model, device, min_sim=min_sim)
        if not cands:
            continue
        best, sim = cands[0]
        matches.append({
            "track_id": best["id"],
            "name": best["name"],
            "kind": "sfx",
            "time_sec": round(t, 3),
            "score": round(sim, 4),
            "top3": [(m["name"], round(s, 3)) for m, s in cands[:3]],
        })
    return impacts, matches
