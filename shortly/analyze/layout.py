"""화면 레이아웃(제목/이미지/자막 영역) 추출·집계.

- OCR 박스 기반: 학습 파이프라인에서 키프레임의 텍스트 박스를 상단/하단으로 군집화해
  제목·자막 영역을, 그 사이를 이미지 영역으로 추정한다.
- Qwen(멀티모달) 기반: 캐시된 키프레임을 비전 모델에 보내 정규화 좌표를 직접 받는다.

좌표는 모두 0~1 정규화(프레임 기준). cy 는 위쪽=0, 아래쪽=1.
"""

from __future__ import annotations

import difflib
import re
import tempfile
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional

from ..llm.base import LLMClient, LLMError, LLMQuotaExceeded, parse_json_loose
from ..utils import log


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _hangul(text: str) -> str:
    return re.sub(r"[^가-힣]", "", text or "")


def _text_similarity(texts: List[str]) -> float:
    """밴드 내 프레임별 텍스트의 평균 유사도.

    제목/브랜드처럼 '고정' 요소는 프레임마다 같은 글자라 유사도가 높고,
    나레이션 '자막'은 프레임마다 내용이 바뀌어 유사도가 낮다. OCR 오탈자에
    강하도록 한글만 남겨 difflib 비율로 비교한다.
    """
    ts = [_hangul(t) for t in texts]
    ts = [t for t in ts if len(t) >= 2]
    if len(ts) < 2:
        return 1.0
    sims: List[float] = []
    for i in range(len(ts)):
        for j in range(i + 1, len(ts)):
            sims.append(difflib.SequenceMatcher(None, ts[i], ts[j]).ratio())
    return mean(sims) if sims else 1.0


def _cluster_bands(frames_items: List[List[Dict]],
                   tol: float = 0.05) -> List[Dict]:
    """모든 프레임의 텍스트 박스를 세로 위치로 군집화해 밴드 목록을 만든다."""
    flat: List[tuple] = []
    for fi, items in enumerate(frames_items):
        for it in items:
            if it.get("cy") is None:
                continue
            flat.append((float(it["cy"]), float(it.get("bh", 0.02)),
                         it.get("text", ""), fi))
    flat.sort(key=lambda x: x[0])
    bands: List[Dict] = []
    for cy, bh, text, fi in flat:
        if bands and cy - bands[-1]["_last_cy"] <= tol:
            b = bands[-1]
        else:
            b = {"cys": [], "bhs": [], "texts": [], "frames": set(),
                 "_last_cy": cy}
            bands.append(b)
        b["cys"].append(cy)
        b["bhs"].append(bh)
        b["texts"].append(text)
        b["frames"].add(fi)
        b["_last_cy"] = cy
    out: List[Dict] = []
    for b in bands:
        top = min(c - h / 2 for c, h in zip(b["cys"], b["bhs"]))
        bot = max(c + h / 2 for c, h in zip(b["cys"], b["bhs"]))
        out.append({
            "cy": round(_clamp(mean(b["cys"])), 3),
            "bh": round(_clamp(mean(b["bhs"])), 3),
            "top": round(_clamp(top), 3),
            "bottom": round(_clamp(bot), 3),
            "occurrence": len(b["frames"]),
            "count": len(b["texts"]),
            "similarity": round(_text_similarity(b["texts"]), 3),
        })
    return out


def derive_layout_from_ocr(frames_items: List[List[Dict]]) -> Optional[Dict]:
    """OCR 박스의 세로 군집 + '프레임 간 텍스트 고정/변화'로 영역을 구분.

    - 제목: 프레임마다 글자가 (거의) 그대로인 '고정' 밴드 중 큰 글씨(브랜드 로고 제외).
    - 자막: 프레임마다 글자가 바뀌는 '변화' 밴드(나레이션).
    - 이미지: 제목/자막 밴드 아래의 영역.
    """
    n = len(frames_items)
    if n == 0:
        return None
    bands = _cluster_bands(frames_items)
    if not bands:
        return None

    # 구조적 요소만: 절반 이상 프레임에 등장(일회성 워터마크/노이즈 제외)
    min_occ = max(2, round(n * 0.5))
    header = [b for b in bands if b["occurrence"] >= min_occ and b["cy"] < 0.5]

    fixed = [b for b in header if b["similarity"] >= 0.5]
    dynamic = [b for b in header if b["similarity"] < 0.5]

    # 제목: 고정 밴드 중 최상단 브랜드(cy<0.12)를 제외하고 가장 큰 글씨
    title_cands = [b for b in fixed if b["cy"] >= 0.12] or fixed
    title_band = max(title_cands, key=lambda b: b["bh"]) if title_cands else None

    # 자막: 변화 밴드 중 (제목 아래이면서) 등장 빈도가 가장 높은 밴드
    cap_cands = dynamic
    if title_band:
        cap_cands = [b for b in dynamic if b["cy"] > title_band["cy"] - 0.02] or dynamic
    caption_band = (max(cap_cands, key=lambda b: (b["occurrence"], b["count"]))
                    if cap_cands else None)

    if title_band is None and caption_band is None:
        return None

    # 이미지 영역: 헤더(제목/자막) 아래 ~ 하단 여백 전까지로 추정
    header_bottom = max([b["bottom"] for b in (title_band, caption_band) if b],
                        default=0.4)
    img_top = _clamp(header_bottom + 0.01)
    img_bot = _clamp(max(img_top + 0.3, min(0.80, img_top + 0.4)))
    image = {
        "cy": round((img_top + img_bot) / 2, 3),
        "h": round(_clamp(img_bot - img_top), 3),
        "w": 0.92,
    }
    return {
        "source": "ocr",
        "samples": n,
        "title": ({"cy": title_band["cy"], "h": title_band["bh"]}
                  if title_band else None),
        "caption": ({"cy": caption_band["cy"], "h": caption_band["bh"]}
                    if caption_band else None),
        "image": image,
    }


_QWEN_LAYOUT_PROMPT = (
    "이 이미지는 유튜브 쇼츠(세로 9:16)의 한 프레임이다. 화면 구성 요소의 위치를 "
    "0~1 정규화 좌표로 알려줘. y 는 위쪽이 0, 아래쪽이 1, x 도 왼쪽이 0, 오른쪽이 1. "
    "cy=세로 중심, cx=가로 중심, h=높이 비율, w=너비 비율.\n"
    "다음 JSON 하나만 출력(설명·코드펜스 금지):\n"
    '{"title":{"cy":n,"h":n},"image":{"cy":n,"h":n,"w":n},"caption":{"cy":n,"h":n}}\n'
    "title=상단에 고정된 제목 텍스트 영역, image=가운데 사진/영상이 채워진 영역, "
    "caption=하단에 나오는 자막 영역. 해당 요소가 없으면 그 값은 null."
)


def _downscale_for_vision(src: Path, long_edge: int = 512) -> Path:
    """비전 토큰 폭증/속도 저하를 막기 위해 키프레임을 축소한 임시 JPEG 를 만든다.

    레이아웃(영역 위치) 판별에는 저해상도로 충분하며, 큰 프레임을 그대로 보내면
    비전 토큰이 수천 개로 늘어 추론이 매우 느려진다. 실패 시 원본 경로 반환.
    """
    try:
        from PIL import Image
    except ImportError:
        return src
    try:
        im = Image.open(src).convert("RGB")
        w, h = im.size
        scale = long_edge / max(w, h)
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="shortly_kf_")
        import os
        os.close(fd)
        im.save(tmp, "JPEG", quality=80)
        return Path(tmp)
    except Exception as exc:  # noqa: BLE001
        log.info("키프레임 축소 실패(원본 사용): %s", exc)
        return src


def _med_sub(samples: List[Dict], key: str, sub: str) -> Optional[float]:
    vals = [s[key][sub] for s in samples
            if isinstance(s.get(key), dict)
            and isinstance(s[key].get(sub), (int, float))]
    return round(float(median(vals)), 3) if vals else None


def extract_layout_qwen(keyframes: List[str], llm: LLMClient,
                        max_frames: int = 4) -> Optional[Dict]:
    """캐시된 키프레임을 Qwen 비전 모델에 보내 레이아웃 좌표를 추출(중앙값)."""
    if not llm.available or not keyframes:
        return None
    import os
    samples: List[Dict] = []
    for kf in keyframes[:max_frames]:
        p = Path(kf)
        if not p.is_file():
            continue
        small = _downscale_for_vision(p)
        try:
            raw = llm.describe_image(small, _QWEN_LAYOUT_PROMPT, temperature=0.1,
                                     max_tokens=500)
            data = parse_json_loose(raw)
        except LLMQuotaExceeded:
            raise
        except (LLMError, ValueError) as exc:
            log.info("Qwen 레이아웃 추출 실패(%s): %s", p.name, exc)
            data = None
        finally:
            if small != p:
                try:
                    os.remove(small)
                except OSError:
                    pass
        if isinstance(data, dict):
            samples.append(data)
    if not samples:
        return None

    def region(key: str, with_w: bool = False) -> Optional[Dict]:
        cy = _med_sub(samples, key, "cy")
        h = _med_sub(samples, key, "h")
        if cy is None and h is None:
            return None
        r = {"cy": cy, "h": h}
        if with_w:
            r["w"] = _med_sub(samples, key, "w") or 0.92
        return r

    return {
        "source": "qwen",
        "samples": len(samples),
        "title": region("title"),
        "caption": region("caption"),
        "image": region("image", with_w=True),
    }


def aggregate_layout(per_short: List[Dict]) -> Optional[Dict]:
    """영상별 layout 을 모아 채널 공통 레이아웃(중앙값)으로 집계."""
    layouts = [s.get("layout") for s in per_short if isinstance(s.get("layout"), dict)]
    layouts = [ly for ly in layouts if ly]
    if not layouts:
        return None

    def region(key: str, with_w: bool = False) -> Optional[Dict]:
        subs = [ly[key] for ly in layouts if isinstance(ly.get(key), dict)]
        cys = [r["cy"] for r in subs if isinstance(r.get("cy"), (int, float))]
        hs = [r["h"] for r in subs if isinstance(r.get("h"), (int, float))]
        if not cys and not hs:
            return None
        r = {"cy": round(float(median(cys)), 3) if cys else None,
             "h": round(float(median(hs)), 3) if hs else None}
        if with_w:
            ws = [r2["w"] for r2 in subs if isinstance(r2.get("w"), (int, float))]
            r["w"] = round(float(median(ws)), 3) if ws else 0.92
        return r

    sources = {ly.get("source") for ly in layouts}
    return {
        "source": "+".join(sorted(s for s in sources if s)),
        "samples": len(layouts),
        "title": region("title"),
        "caption": region("caption"),
        "image": region("image", with_w=True),
    }
