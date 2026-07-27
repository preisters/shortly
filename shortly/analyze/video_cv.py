"""영상 편집 기법 분석(CV).

- 컷/장면 전환 감지(PySceneDetect 우선, 없으면 프레임 차분)
- 전환 종류 추정(하드컷 / 디졸브·페이드)
- 모션·줌 추정(옵티컬 플로우 크기 및 발산)
- 화면 텍스트(자막) 존재 비율 및 대표 프레임 추출

opencv 미설치 시 전체 분석을 건너뛰고 사유를 반환합니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..utils import log, try_import

# 샷 내부 평균 프레임차분이 이 값 이상이면 '영상/움짤'(motion_clip), 미만이면 '정지 이미지'.
# 160x90 그레이의 평균 절대차 기준(정지 이미지는 압축 노이즈로 ~1 이하, 움직임은 수치↑).
# 실제 상위 영상으로 튜닝이 필요할 수 있는 값(보수적으로 설정).
_STILL_DIFF_THRESH = 2.5


def _sample_gray_frames(cv2, cap, target_fps: float, max_frames: int = 1200):
    """일정 간격으로 다운스케일 그레이 프레임을 표본 추출."""
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        return [], src_fps, 0
    stride = max(1, int(round(src_fps / target_fps)))
    frames = []
    times = []
    idx = 0
    while idx < total and len(frames) < max_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (160, 90))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        frames.append(gray)
        times.append(idx / src_fps)
        idx += stride
    return frames, src_fps, total


def _detect_cuts_scenedetect(video_path: Path) -> Optional[List[float]]:
    sd = try_import("scenedetect")
    if sd is None:
        return None
    try:
        from scenedetect import detect, ContentDetector  # type: ignore

        scenes = detect(str(video_path), ContentDetector())
        return [s[0].get_seconds() for s in scenes if s[0].get_seconds() > 0]
    except Exception as exc:  # noqa: BLE001
        log.info("scenedetect 실패, 차분 방식으로 대체: %s", exc)
        return None


def _classify_transition(diffs, cut_idx: int) -> str:
    """컷 주변 diff 패턴으로 전환 종류 추정."""
    import numpy as np

    lo = max(0, cut_idx - 3)
    hi = min(len(diffs), cut_idx + 3)
    window = diffs[lo:hi]
    if not len(window):
        return "cut"
    peak = float(np.max(window))
    above = int(np.sum(np.array(window) > peak * 0.5))
    # 넓게 퍼진 상승 = 디졸브/페이드, 뾰족한 단일 스파이크 = 하드컷
    return "dissolve" if above >= 3 else "cut"


def _radial_divergence(cv2, np, a, b) -> Optional[float]:
    """두 그레이 프레임 사이 옵티컬 플로우의 방사(발산) 평균. 양수=줌인 경향."""
    try:
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 2, 13, 3, 5, 1.1, 0)
    except Exception:  # noqa: BLE001
        return None
    fx, fy = flow[..., 0], flow[..., 1]
    h, w = fx.shape
    cy, cx = h / 2.0, w / 2.0
    ys, xs = np.mgrid[0:h, 0:w]
    rx, ry = xs - cx, ys - cy
    norm = np.sqrt(rx * rx + ry * ry) + 1e-6
    radial = (fx * rx + fy * ry) / norm
    return float(np.mean(radial))


def _analyze_shots(cv2, np, frames, diffs, cut_times, transitions,
                   duration: float, sample_dt: float) -> List[Dict]:
    """컷 경계로 프레임을 버킷팅해 샷별 편집 특성을 산출.

    - media_type: 샷 내부 평균 프레임차분으로 정지 이미지 vs 영상/움짤 판별.
    - zoom_dir: 샷 내부 옵티컬 플로우 발산 부호로 줌인/줌아웃/고정.
    - transition_in: 그 샷을 여는 컷의 전환 종류(cut/dissolve).
    단일 프레임으로는 알 수 없는 정보를 멀티프레임 비교로 채운다.
    """
    n = len(frames)
    if n < 2:
        return []
    boundaries = [0.0] + list(cut_times) + [float(duration)]
    trans_by_time = {round(float(t.get("time", 0)), 2): t.get("type", "cut")
                     for t in (transitions or [])}
    shots: List[Dict] = []
    idx = 0
    for si in range(len(boundaries) - 1):
        start, end = boundaries[si], boundaries[si + 1]
        if end - start < 0.2:
            continue
        # 컷 직후 전환 블렌드 프레임을 건너뛰어 오탐(전환→움짤 오인) 방지
        i0 = min(max(0, int(round(start / sample_dt)) + 1), n - 1)
        i1 = min(max(i0 + 1, int(round(end / sample_dt)) - 1), n)
        in_diffs = [diffs[j] for j in range(i0, min(i1 - 1, len(diffs)))]
        mean_diff = float(np.mean(in_diffs)) if in_diffs else 0.0
        media_type = ("motion_clip" if mean_diff >= _STILL_DIFF_THRESH
                      else "still_image")
        zoom_dir = "static"
        if i1 - i0 >= 2:
            mid = i0 + (i1 - i0) // 2
            rad = _radial_divergence(cv2, np, frames[i0], frames[min(mid, n - 1)])
            if rad is not None:
                zoom_dir = ("zoom_in" if rad > 0.15
                            else ("zoom_out" if rad < -0.15 else "static"))
        transition_in = None
        if si >= 1:
            transition_in = trans_by_time.get(round(float(start), 2), "cut")
        shots.append({
            "index": idx,
            "start": round(start, 2),
            "end": round(end, 2),
            "media_type": media_type,
            "zoom_dir": zoom_dir,
            "transition_in": transition_in,
            "motion": round(mean_diff, 2),
        })
        idx += 1
    return shots


def analyze_video(video_path: Path, frames_out: Optional[Path] = None,
                  keyframe_count: int = 6, detailed: bool = False) -> Dict:
    """단일 영상의 편집 기법을 분석합니다.

    detailed=True 이면 (상위 영상 전용) 컷 경계로 버킷팅한 샷별 분석(`shots`:
    media_type/zoom_dir/transition_in)을 추가로 산출합니다. 기본값(False)은
    기존 동작과 동일하며 추가 비용이 없습니다.
    """
    cv2 = try_import("cv2")
    if cv2 is None:
        return {"available": False, "reason": "opencv 미설치"}
    np = try_import("numpy")
    if np is None:
        return {"available": False, "reason": "numpy 미설치"}

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {"available": False, "reason": "영상 열기 실패"}

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frames, src_fps, total = _sample_gray_frames(cv2, cap, target_fps=10.0)
    duration = (total / src_fps) if src_fps else 0.0
    if len(frames) < 2:
        cap.release()
        return {"available": False, "reason": "프레임 부족"}

    sample_dt = 1.0 / 10.0

    # --- 프레임 차분 ---
    diffs = []
    for a, b in zip(frames[:-1], frames[1:]):
        diffs.append(float(np.mean(np.abs(a.astype("int16") - b.astype("int16")))))
    diffs_arr = np.array(diffs)

    # --- 컷 감지 ---
    cut_times = _detect_cuts_scenedetect(video_path)
    transitions: List[Dict] = []
    if cut_times is None:
        thresh = float(np.mean(diffs_arr) + 2.2 * np.std(diffs_arr))
        thresh = max(thresh, 12.0)
        cut_times = []
        for i in range(1, len(diffs_arr) - 1):
            if diffs_arr[i] > thresh and diffs_arr[i] >= diffs_arr[i - 1] and diffs_arr[i] >= diffs_arr[i + 1]:
                t = (i + 1) * sample_dt
                cut_times.append(round(t, 2))
                transitions.append({"time": round(t, 2), "type": _classify_transition(diffs, i)})
    else:
        cut_times = [round(t, 2) for t in cut_times]
        for t in cut_times:
            i = int(t / sample_dt)
            transitions.append({"time": t, "type": _classify_transition(diffs, min(i, len(diffs) - 1))})

    # --- 샷 길이 통계 ---
    boundaries = [0.0] + cut_times + [duration]
    shot_lengths = [round(boundaries[i + 1] - boundaries[i], 2)
                    for i in range(len(boundaries) - 1) if boundaries[i + 1] > boundaries[i]]
    num_cuts = len(cut_times)
    avg_shot = round(sum(shot_lengths) / len(shot_lengths), 2) if shot_lengths else duration
    cuts_per_min = round(num_cuts / (duration / 60.0), 1) if duration else 0.0

    # --- 모션/줌(옵티컬 플로우) ---
    motion_vals: List[float] = []
    zoom_vals: List[float] = []
    step = max(1, len(frames) // 60)  # 최대 ~60구간
    for i in range(0, len(frames) - step, step):
        try:
            flow = cv2.calcOpticalFlowFarneback(
                frames[i], frames[i + step], None, 0.5, 2, 13, 3, 5, 1.1, 0)
        except Exception:  # noqa: BLE001
            continue
        fx, fy = flow[..., 0], flow[..., 1]
        mag = float(np.mean(np.sqrt(fx * fx + fy * fy)))
        motion_vals.append(mag)
        # 발산(divergence) 근사: 중심 대비 바깥으로 향하면 줌인
        h, w = fx.shape
        cy, cx = h / 2.0, w / 2.0
        ys, xs = np.mgrid[0:h, 0:w]
        rx, ry = xs - cx, ys - cy
        norm = np.sqrt(rx * rx + ry * ry) + 1e-6
        radial = (fx * rx + fy * ry) / norm
        zoom_vals.append(float(np.mean(radial)))

    motion_mean = round(float(np.mean(motion_vals)), 3) if motion_vals else 0.0
    motion_peak = round(float(np.max(motion_vals)), 3) if motion_vals else 0.0
    zoom_mean = round(float(np.mean(zoom_vals)), 3) if zoom_vals else 0.0
    zoom_tendency = "zoom_in" if zoom_mean > 0.15 else ("zoom_out" if zoom_mean < -0.15 else "static")

    # --- 화면 텍스트 존재 비율(엣지 밀도 휴리스틱) ---
    text_frames = 0
    for gray in frames[:: max(1, len(frames) // 40)]:
        edges = cv2.Canny(gray, 80, 160)
        # 하단 1/3 영역 엣지 밀도로 자막 존재 추정
        band = edges[int(gray.shape[0] * 0.6):, :]
        if float(np.mean(band > 0)) > 0.06:
            text_frames += 1
    sampled = len(frames[:: max(1, len(frames) // 40)])
    text_ratio = round(text_frames / sampled, 2) if sampled else 0.0

    # --- 대표 프레임 추출 ---
    keyframes: List[str] = []
    if frames_out is not None:
        frames_out.mkdir(parents=True, exist_ok=True)
        # 균등 분포 + 컷 직후 프레임 우선
        pick_times = sorted(set(
            [duration * k / (keyframe_count + 1) for k in range(1, keyframe_count + 1)]
            + [t + 0.2 for t in cut_times[:keyframe_count]]
        ))[:keyframe_count]
        cap2 = cv2.VideoCapture(str(video_path))
        for k, t in enumerate(pick_times):
            fi = int(t * src_fps)
            cap2.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap2.read()
            if not ok:
                continue
            out = frames_out / f"kf_{k:02d}_{t:.1f}s.jpg"
            cv2.imwrite(str(out), frame)
            keyframes.append(str(out))
        cap2.release()

    pacing = ("very_fast" if cuts_per_min >= 45 else
              "fast" if cuts_per_min >= 25 else
              "medium" if cuts_per_min >= 12 else "slow")

    # --- 샷별 상세 분석(상위 영상 전용, 멀티프레임 비교) ---
    shots: List[Dict] = []
    if detailed:
        shots = _analyze_shots(cv2, np, frames, diffs, cut_times, transitions,
                               duration, sample_dt)

    cap.release()

    result = {
        "available": True,
        "duration_sec": round(duration, 2),
        "resolution": [width, height],
        "orientation": "portrait" if height >= width else "landscape",
        "fps": round(src_fps, 2),
        "num_cuts": num_cuts,
        "cuts_per_min": cuts_per_min,
        "avg_shot_sec": avg_shot,
        "shot_lengths": shot_lengths,
        "cut_times": cut_times,
        "pacing": pacing,
        "transitions": transitions,
        "transition_summary": _summarize_transitions(transitions),
        "motion_mean": motion_mean,
        "motion_peak": motion_peak,
        "zoom_tendency": zoom_tendency,
        "text_overlay_ratio": text_ratio,
        "keyframes": keyframes,
    }
    if detailed:
        result["shots"] = shots
    return result


def _summarize_transitions(transitions: List[Dict]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for t in transitions:
        summary[t["type"]] = summary.get(t["type"], 0) + 1
    return summary
