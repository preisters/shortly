"""Shazam식 랜드마크 오디오 지문(landmark fingerprinting).

- 참조 효과음 라이브러리로 지문 DB를 만들고,
- 영상 오디오(가급적 보컬 감쇠 스템)에서 슬라이딩 매칭으로
  '어떤 효과음이 몇 초에 등장했는지'를 신뢰도 점수와 함께 찾습니다.

numpy 필수, scipy 있으면 피크 검출이 더 정확합니다(없어도 동작).
ffmpeg 로 오디오를 디코딩합니다.
"""

from __future__ import annotations

import pickle
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import Settings, get_settings
from ..utils import decode_audio_mono, log

# --- 지문 파라미터 ---
N_FFT = 1024
HOP = 512
PEAK_NEIGH_F = 20        # 주파수 방향 피크 이웃 크기
PEAK_NEIGH_T = 20        # 시간 방향 피크 이웃 크기
AMP_PERCENTILE = 70      # 이 백분위 이상 진폭만 피크 후보
FAN_VALUE = 12           # 앵커당 페어링할 후속 피크 수
MIN_DT = 1               # 페어 최소 시간 간격(프레임)
MAX_DT = 40              # 페어 최대 시간 간격(프레임)
F_BITS = 10              # 주파수 비트(0..1023)


@dataclass
class Detection:
    track_id: int
    name: str
    kind: str
    time_sec: float       # 영상 내 등장 추정 시각
    votes: int            # 정합 해시 수
    score: float          # 0..1 정규화 신뢰도

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "name": self.name,
            "kind": self.kind,
            "time_sec": round(self.time_sec, 3),
            "votes": self.votes,
            "score": round(self.score, 4),
        }


def _sec_per_frame(sr: int) -> float:
    return HOP / float(sr)


def _magnitude_spectrogram(samples, sr):
    import numpy as np

    if samples is None or len(samples) < N_FFT:
        return None
    # scipy 우선
    try:
        from scipy.signal import stft  # type: ignore

        _, _, zxx = stft(samples, fs=sr, nperseg=N_FFT, noverlap=N_FFT - HOP,
                         boundary=None, padded=False)
        return np.abs(zxx)
    except Exception:  # noqa: BLE001 - scipy 미설치 등
        pass
    # numpy 수동 STFT
    win = np.hanning(N_FFT).astype(np.float32)
    n_frames = 1 + (len(samples) - N_FFT) // HOP
    if n_frames <= 0:
        return None
    spec = np.empty((N_FFT // 2 + 1, n_frames), dtype=np.float32)
    for i in range(n_frames):
        seg = samples[i * HOP: i * HOP + N_FFT] * win
        spec[:, i] = np.abs(np.fft.rfft(seg))
    return spec


def _find_peaks(spec) -> List[Tuple[int, int]]:
    """(t_frame, f_bin) 피크 좌표 리스트."""
    import numpy as np

    if spec is None or spec.size == 0:
        return []
    thresh = np.percentile(spec, AMP_PERCENTILE)
    try:
        from scipy.ndimage import maximum_filter  # type: ignore

        local_max = maximum_filter(spec, size=(PEAK_NEIGH_F, PEAK_NEIGH_T)) == spec
        mask = local_max & (spec > thresh)
        f_idx, t_idx = np.where(mask)
        return list(zip(t_idx.tolist(), f_idx.tolist()))
    except Exception:  # noqa: BLE001
        pass
    # numpy 대체: 시간 프레임별 상위 피크 몇 개만 취함
    peaks: List[Tuple[int, int]] = []
    n_freq, n_frames = spec.shape
    for t in range(n_frames):
        col = spec[:, t]
        cand = np.where(col > thresh)[0]
        if cand.size == 0:
            continue
        top = cand[np.argsort(col[cand])[-6:]]
        for f in top.tolist():
            peaks.append((t, f))
    return peaks


def _hashes(peaks: List[Tuple[int, int]]):
    """피크 목록 -> (hash_int, anchor_t) 제너레이터."""
    peaks = sorted(peaks)  # 시간순
    n = len(peaks)
    for i in range(n):
        t1, f1 = peaks[i]
        f1 &= (1 << F_BITS) - 1
        for j in range(1, FAN_VALUE + 1):
            if i + j >= n:
                break
            t2, f2 = peaks[i + j]
            dt = t2 - t1
            if dt < MIN_DT or dt > MAX_DT:
                continue
            f2 &= (1 << F_BITS) - 1
            h = (f1 << (F_BITS + 8)) | (f2 << 8) | (dt & 0xFF)
            yield h, t1


def fingerprint_samples(samples, sr: int):
    spec = _magnitude_spectrogram(samples, sr)
    peaks = _find_peaks(spec)
    return list(_hashes(peaks))


class FingerprintIndex:
    """효과음 지문 DB."""

    def __init__(self, sr: int = 11025) -> None:
        self.sr = sr
        self.db: Dict[int, List[Tuple[int, int]]] = defaultdict(list)  # hash -> [(t, track_id)]
        self.tracks: Dict[int, dict] = {}  # track_id -> meta

    # --- 구축 ---
    def add_track(self, track_id: int, meta: dict, samples) -> int:
        hashes = fingerprint_samples(samples, self.sr)
        for h, t in hashes:
            self.db[h].append((t, track_id))
        self.tracks[track_id] = meta
        return len(hashes)

    def build_from_catalog(self, items, kinds=("sfx",),
                           max_items: Optional[int] = None) -> None:
        count = 0
        selected = [it for it in items if it.kind in kinds]
        if max_items:
            selected = selected[:max_items]
        total = len(selected)
        for i, it in enumerate(selected, start=1):
            samples = decode_audio_mono(Path(it.path), sr=self.sr)
            if samples is None:
                continue
            n = self.add_track(it.id, {
                "id": it.id, "name": it.name, "filename": it.filename,
                "kind": it.kind, "path": it.path,
            }, samples)
            count += 1
            if i % 25 == 0 or i == total:
                log.info("지문 구축: %d/%d (누적 해시 %d)", i, total, len(self.db))
        log.info("지문 DB 완료: 트랙 %d개, 해시 %d개", count, len(self.db))

    # --- 저장/로드 ---
    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump({
                "sr": self.sr,
                "db": {k: v for k, v in self.db.items()},
                "tracks": self.tracks,
            }, f, protocol=pickle.HIGHEST_PROTOCOL)
        log.info("지문 DB 저장: %s", path)

    @classmethod
    def load(cls, path: Path) -> Optional["FingerprintIndex"]:
        path = Path(path)
        if not path.is_file():
            return None
        with path.open("rb") as f:
            data = pickle.load(f)
        idx = cls(sr=data.get("sr", 11025))
        idx.db = defaultdict(list, data["db"])
        idx.tracks = data["tracks"]
        return idx

    @classmethod
    def default_path(cls, settings: Optional[Settings] = None) -> Path:
        settings = settings or get_settings()
        return settings.sfx_index_dir / "fingerprints.pkl"

    # --- 질의 ---
    def query(self, samples, min_votes: int = 5, offset_tol: int = 2,
              max_per_track: int = 3) -> List[Detection]:
        """영상 오디오에서 효과음 등장 구간을 찾습니다."""
        if not self.db:
            return []
        q_hashes = fingerprint_samples(samples, self.sr)
        if not q_hashes:
            return []
        # (track_id, offset_bin) -> votes
        votes: Dict[Tuple[int, int], int] = defaultdict(int)
        track_hash_totals: Dict[int, int] = defaultdict(int)
        for h, q_t in q_hashes:
            bucket = self.db.get(h)
            if not bucket:
                continue
            for db_t, track_id in bucket:
                offset = db_t - q_t
                # offset 을 tolerance 단위로 양자화해 흔들림 흡수
                offset_bin = int(round(offset / max(1, offset_tol))) * max(1, offset_tol)
                votes[(track_id, offset_bin)] += 1
        # 트랙별 총 해시 수(정규화용)
        for h, bucket in self.db.items():
            for _t, track_id in bucket:
                track_hash_totals[track_id] += 1

        spf = _sec_per_frame(self.sr)
        # 트랙별 상위 offset bin 선별
        by_track: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        for (track_id, offset_bin), v in votes.items():
            if v >= min_votes:
                by_track[track_id].append((offset_bin, v))

        detections: List[Detection] = []
        for track_id, bins in by_track.items():
            bins.sort(key=lambda x: x[1], reverse=True)
            meta = self.tracks.get(track_id, {})
            total = max(1, track_hash_totals.get(track_id, 1))
            for offset_bin, v in bins[:max_per_track]:
                # 등장 시각: query_t = db_t - offset. 트랙 시작(db_t≈0) -> query_t ≈ -offset
                time_sec = max(0.0, -offset_bin * spf)
                score = min(1.0, v / (total ** 0.5 + 1e-6))
                detections.append(Detection(
                    track_id=track_id,
                    name=meta.get("name", str(track_id)),
                    kind=meta.get("kind", "sfx"),
                    time_sec=time_sec,
                    votes=v,
                    score=round(score, 4),
                ))
        detections.sort(key=lambda d: d.time_sec)
        return detections
