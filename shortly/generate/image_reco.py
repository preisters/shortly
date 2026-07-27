"""가이드 컷별 이미지/매체/효과 추천.

학습된 채널의 image_style(사용 이미지 경향) + editing_style(영상·움짤 비율/줌/전환)과
각 세그먼트의 나레이션·의도를 근거로, 컷마다 어떤 이미지를 어떤 매체(정지/영상·움짤)로
어떤 효과와 함께 넣을지 추천한다. LLM 우선, 실패 시 휴리스틱.
"""

from __future__ import annotations

from typing import Dict, List

from ..llm.base import LLMClient, LLMError
from ..utils import log


def _cut_count(seg: Dict) -> int:
    return max(1, min(int(seg.get("cuts_suggested") or 1), 6))


def _fit(items: List[str], n: int, filler: str) -> List[str]:
    """리스트를 정확히 n개로 맞춘다(부족하면 filler/마지막값으로 채움)."""
    items = [str(x) for x in (items or []) if str(x).strip()]
    if not items:
        items = [filler]
    while len(items) < n:
        items.append(items[-1])
    return items[:n]


def _heur_media(editing_style: Dict, n: int) -> List[str]:
    """영상·움짤 비율에 맞춰 컷별 매체(사진 vs 영상/움짤) 배분."""
    ratio = float(editing_style.get("motion_clip_ratio") or 0.0)
    n_clip = int(round(n * ratio))
    out: List[str] = []
    for c in range(n):
        out.append("영상/움짤" if c < n_clip else "사진")
    return out


def _heur_effects(editing_style: Dict, n: int) -> List[str]:
    """채널이 실제 쓰는 효과 위주로 컷별 효과 배분."""
    effects = list(editing_style.get("capcut_effects") or [])
    if not effects:
        dz = editing_style.get("dominant_zoom")
        if dz == "zoom_in":
            effects = ["줌 인"]
        elif dz == "zoom_out":
            effects = ["줌 아웃"]
        else:
            effects = ["없음"]
    return [effects[c % len(effects)] for c in range(n)]


def _heur_images(seg: Dict, image_style: Dict, n: int) -> List[str]:
    """의도/주제 + 채널 이미지 경향으로 간단한 이미지 추천 문구 생성."""
    subjects = list(image_style.get("subjects") or [])
    intent = (seg.get("intent") or "").strip()
    base = f"{intent} 관련 이미지" if intent else "핵심 장면 이미지"
    out: List[str] = []
    for c in range(n):
        if c < len(subjects):
            out.append(subjects[c])
        else:
            out.append(base)
    return out


def _recommend_llm(llm: LLMClient, image_style: Dict, editing_style: Dict,
                   segments: List[Dict]) -> Dict:
    """세그먼트별 추천을 한 번의 LLM 호출로 생성. {index: {...}} 반환."""
    seg_lines = []
    for s in segments:
        seg_lines.append(
            f"- index {s.get('index')}: 컷 {_cut_count(s)}개 / 의도 "
            f"'{s.get('intent', '')}' / 나레이션 \"{(s.get('narration') or '')[:60]}\""
        )
    system = (
        "너는 유튜브 쇼츠 편집 디렉터다. 학습된 채널의 이미지/편집 경향에 맞춰 각 "
        "세그먼트의 컷마다 넣을 이미지, 매체(사진|영상/움짤), 효과를 추천한다. "
        "효과와 매체는 반드시 채널이 실제로 쓰는 범위에서 고른다. 반드시 JSON 하나만 출력한다."
    )
    schema = (
        '{"segments": [{"index": number, '
        '"image_suggestions": string[], "media_suggestions": string[], '
        '"effect_suggestions": string[]}]} '
        "(각 배열 길이는 그 세그먼트의 컷 수와 정확히 같게)"
    )
    user = (
        f"[채널 이미지 경향] {image_style.get('summary', '')} / 타입 "
        f"{image_style.get('common_types', [])}\n"
        f"[채널 편집 경향] 영상·움짤 비율 {editing_style.get('motion_clip_ratio')}, "
        f"줌 {editing_style.get('dominant_zoom')}, 전환 "
        f"{editing_style.get('transition_types')}, 권장 효과 "
        f"{editing_style.get('capcut_effects')}\n"
        f"[세그먼트]\n" + "\n".join(seg_lines) + "\n\n"
        f"매체는 '사진' 또는 '영상/움짤'로만. 효과는 '줌 인/줌 아웃/페이드 인/페이드 아웃/없음' 등 "
        f"채널 실제 사용 효과 위주.\n"
        f"스키마: {schema}"
    )
    data = llm.complete_json(system, user, max_tokens=4000)
    out: Dict = {}
    if isinstance(data, dict):
        for item in data.get("segments", []) or []:
            if isinstance(item, dict) and item.get("index") is not None:
                out[int(item["index"])] = item
    return out


def recommend_cuts(llm: LLMClient, profile: Dict, guide: Dict) -> Dict:
    """가이드 각 세그먼트에 image/media/effect 추천을 채워 넣고 guide 를 반환."""
    image_style = profile.get("image_style") or {}
    editing_style = profile.get("editing_style") or {}
    segments = guide.get("segments") or []
    if not segments:
        return guide

    llm_reco: Dict = {}
    if getattr(llm, "available", False):
        try:
            llm_reco = _recommend_llm(llm, image_style, editing_style, segments)
        except (LLMError, ValueError) as exc:
            log.info("컷별 추천 LLM 실패, 휴리스틱 사용: %s", exc)

    for seg in segments:
        n = _cut_count(seg)
        rec = llm_reco.get(seg.get("index"), {}) if isinstance(seg.get("index"), int) else {}
        images = rec.get("image_suggestions") or _heur_images(seg, image_style, n)
        media = rec.get("media_suggestions") or _heur_media(editing_style, n)
        effects = rec.get("effect_suggestions") or _heur_effects(editing_style, n)
        seg["image_suggestions"] = _fit(images, n, "핵심 장면 이미지")
        seg["media_suggestions"] = _fit(media, n, "사진")
        seg["effect_suggestions"] = _fit(effects, n, "없음")
    return guide
