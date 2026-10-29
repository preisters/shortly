"""컷(샷)별 이미지 '내용' 분석: 이미지 영역만 크롭해 비전 LLM으로 구조화 추출.

역할 분담(중요): 영상/움짤 여부나 전환 효과는 여기서 판단하지 않는다(그 판단은
video_cv 의 detailed shots 가 멀티프레임 비교로 담당). 이 모듈은 각 샷의 이미지가
'무엇을 담고 있는지'(피사체/타입/자막 연관성)만 본다.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from ..llm.base import LLMClient, LLMError, LLMQuotaExceeded, parse_json_loose
from ..utils import log, try_import

_IMG_USAGE_PROMPT = (
    "/no_think\n"
    "이 이미지는 유튜브 쇼츠 영상의 '이미지 영역'을 잘라낸 것이다. 이 이미지가 "
    "'무엇을 담고 있는지'만 한국어로 간단히 분석하라. 움직임/영상 여부나 전환 효과는 "
    "판단하지 말 것(그건 별도 분석 담당).\n"
    "다음 JSON 하나만 출력(설명·코드펜스 금지):\n"
    '{"content": string(한 문장 설명), '
    '"image_type": string(사진|뉴스캡처|일러스트|제품|인물|기타 중 하나), '
    '"relevant_to_caption": boolean(영상 주제/나레이션과 관련 있으면 true), '
    '"note": string(유명 일화·사건 등 참고할 점이 있으면 적고, 없으면 빈 문자열)}'
)


def _shot_midpoints(cv_result: Dict, max_frames: int) -> List[float]:
    """컷 경계로 각 샷 중앙 시각을 산출. 컷 정보가 없으면 균등 8프레임 폴백."""
    duration = float(cv_result.get("duration_sec") or 0.0)
    if duration <= 0:
        return []
    cut_times = cv_result.get("cut_times") or []
    times: List[float] = []
    if cut_times:
        boundaries = [0.0] + [float(t) for t in cut_times] + [duration]
        for i in range(len(boundaries) - 1):
            a, b = boundaries[i], boundaries[i + 1]
            if b - a < 0.2:
                continue
            times.append(round((a + b) / 2.0, 2))
    if not times:
        k = 8
        times = [round(duration * (j + 0.5) / k, 2) for j in range(k)]
    # 과도한 비용 방지: 상한을 넘으면 균등 서브샘플링
    if len(times) > max_frames:
        step = len(times) / float(max_frames)
        times = [times[int(j * step)] for j in range(max_frames)]
    return times


def _fps_times(cv_result: Dict, fps: float, max_frames: int) -> List[float]:
    """영상 전체를 초당 fps 장으로 촘촘히 샘플링한 시각 목록(내용 누락 방지)."""
    duration = float(cv_result.get("duration_sec") or 0.0)
    if duration <= 0 or fps <= 0:
        return []
    n = max(1, int(duration * fps))
    times = [round((k + 0.5) / fps, 2) for k in range(n) if (k + 0.5) / fps < duration]
    if len(times) > max_frames:
        step = len(times) / float(max_frames)
        times = [times[int(j * step)] for j in range(max_frames)]
    return times


def _shot_for_time(shots: List[Dict], t: float) -> Optional[Dict]:
    """시각 t 가 속한 CV 샷을 찾는다(편집기법 필드 결합용)."""
    for sh in shots or []:
        s, e = sh.get("start"), sh.get("end")
        if isinstance(s, (int, float)) and isinstance(e, (int, float)) and s <= t < e:
            return sh
    return None


def _crop_image_region(im, layout: Optional[Dict]):
    """정규화된 image 영역(cy,h,w)으로 크롭. 정보 없으면 원본 반환."""
    reg = layout.get("image") if isinstance(layout, dict) else None
    if not isinstance(reg, dict):
        return im
    cy, h = reg.get("cy"), reg.get("h")
    w = reg.get("w") or 0.92
    if not isinstance(cy, (int, float)) or not isinstance(h, (int, float)):
        return im
    width, height = im.size
    top = max(0.0, cy - h / 2.0)
    bot = min(1.0, cy + h / 2.0)
    left = max(0.0, 0.5 - w / 2.0)
    right = min(1.0, 0.5 + w / 2.0)
    box = (int(left * width), int(top * height),
           int(right * width), int(bot * height))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return im
    return im.crop(box)


def _save_downscaled(im, long_edge: int = 512) -> str:
    """크롭 이미지를 다운스케일해 임시 JPEG 로 저장(비전 토큰/속도 완화)."""
    im = im.convert("RGB")
    w, h = im.size
    scale = long_edge / float(max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="shortly_img_")
    os.close(fd)
    im.save(tmp, "JPEG", quality=80)
    return tmp


def analyze_image_usage(llm: LLMClient, video_path: Path, cv_result: Dict,
                        layout: Optional[Dict] = None, narration_text: str = "",
                        max_frames: int = 24, sample_fps: Optional[float] = None) -> List[Dict]:
    """상위 영상의 이미지 내용을 비전 LLM 으로 구조화 추출.

    sample_fps 가 주어지면(예: 2.0) 영상 전체를 초당 그만큼 촘촘히 샘플링해 내용
    누락을 막고(줌아웃 끝 구도·컷 내부 변화 포착), 각 프레임을 CV 샷에 조인해 편집기법
    (media_type/zoom_dir/transition_in)을 함께 붙인다. 없으면 컷당 1프레임(중앙).

    반환: [{shot_index, time, content, image_type, relevant_to_caption, note,
            media_type, zoom_dir, transition_in}]
    사용 불가(LLM/opencv/PIL 없음)하면 빈 리스트.
    """
    if not getattr(llm, "available", False):
        return []
    cv2 = try_import("cv2")
    if cv2 is None:
        return []
    try:
        from PIL import Image
    except ImportError:
        return []

    shots = cv_result.get("shots") or []
    if sample_fps and sample_fps > 0:
        # fps 모드는 프레임이 많아질 수 있어 상한을 넉넉히
        times = _fps_times(cv_result, sample_fps, max(max_frames, 600))
    else:
        times = _shot_midpoints(cv_result, max_frames)
    if not times:
        return []

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    prompt = _IMG_USAGE_PROMPT
    if narration_text:
        prompt += f"\n[영상 나레이션/주제]\n{narration_text[:400]}"

    out: List[Dict] = []
    for fi, t in enumerate(times):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * src_fps))
        ok, frame = cap.read()
        if not ok:
            continue
        try:
            # BGR(OpenCV) -> RGB(PIL)
            im = Image.fromarray(frame[:, :, ::-1])
        except Exception as exc:  # noqa: BLE001
            log.info("프레임 변환 실패(t=%.1f): %s", t, exc)
            continue
        im = _crop_image_region(im, layout)
        tmp = _save_downscaled(im)
        # 이 프레임이 속한 CV 샷의 편집기법을 결합(줌아웃 등은 샷 전체로 판정됨)
        sh = _shot_for_time(shots, t)
        rec = {
            "shot_index": sh.get("index") if sh else fi,
            "time": t,
            "content": "", "image_type": "기타",
            "relevant_to_caption": True, "note": "",
            "media_type": sh.get("media_type") if sh else None,
            "zoom_dir": sh.get("zoom_dir") if sh else None,
            "transition_in": sh.get("transition_in") if sh else None,
        }
        try:
            raw = llm.describe_image(Path(tmp), prompt, temperature=0.2,
                                     max_tokens=500)
            data = parse_json_loose(raw)
            if isinstance(data, dict):
                rec["content"] = str(data.get("content", "") or "")[:200]
                rec["image_type"] = str(data.get("image_type", "기타") or "기타")[:20]
                rec["relevant_to_caption"] = bool(
                    data.get("relevant_to_caption", True))
                rec["note"] = str(data.get("note", "") or "")[:200]
        except LLMQuotaExceeded:
            raise
        except (LLMError, ValueError) as exc:
            log.info("이미지 내용 분석 실패(t=%.1f): %s", t, exc)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if rec["content"]:
            out.append(rec)
    cap.release()
    return out
