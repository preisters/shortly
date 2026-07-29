"""가이드 컷별 이미지/매체/효과 추천.

학습된 채널의 image_style(사용 이미지 경향) + editing_style(영상·움짤 비율/줌/전환)과
각 세그먼트의 나레이션·의도를 근거로, 컷마다 어떤 이미지를 어떤 매체(정지/영상·움짤)로
어떤 효과와 함께 넣을지 추천한다. LLM 우선, 실패 시 휴리스틱.
"""

from __future__ import annotations

from typing import Dict, List

from ..llm.base import LLMClient, LLMError
from ..utils import log

_ASSET_TYPES = {
    "real_image",
    "stock_image",
    "generated_image",
    "generated_video",
    "meme",
    "screenshot",
    "manual_required",
}


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
        "세그먼트의 컷마다 넣을 이미지, 매체(사진|영상/움짤), 효과와 에셋 확보 방식을 "
        "추천한다. 효과와 매체는 반드시 채널이 실제로 쓰는 범위에서 고른다. "
        "사실성은 이야기 자체가 사실인지가 아니라 그 화면이 실제 원본·동일한 외형이어야 "
        "하는지를 기준으로 판단한다. 반드시 JSON 하나만 출력한다."
    )
    schema = (
        '{"segments": [{"index": number, '
        '"image_suggestions": string[], "media_suggestions": string[], '
        '"effect_suggestions": string[], '
        '"asset_type_suggestions": '
        '("real_image"|"stock_image"|"generated_image"|"generated_video"|'
        '"meme"|"screenshot"|"manual_required")[], '
        '"fact_sensitive_suggestions": boolean[], '
        '"asset_reason_suggestions": string[], '
        '"stock_query_suggestions": string[]}]} '
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
        f"채널 실제 사용 효과 위주.\n\n"
        f"[에셋 분류 원칙]\n"
        f"- generated_image/video: 실제 기록 화면이 존재하지 않는 재현·연상·상황극·상징·합성·과장 연출. "
        f"사실을 설명하는 대본이어도 화면이 재현 장면이면 생성형을 선택한다.\n"
        f"- real_image: 실제 제품, 포장, 로고, 특정 실존 인물처럼 외형이 정확해야 하는 원본 사진.\n"
        f"- stock_image: 세계 스톡 사이트에서 흔히 검색되는 일반 도시·거리·풍경·손·사무실처럼 "
        f"특정 지역·문화·브랜드의 정확한 재현이 필요 없는 장면에만 사용한다.\n"
        f"- 한국 고유 제품·가게·가정·학교·회사·인물 분위기처럼 한국적 디테일이 결과의 핵심인데 "
        f"세계 스톡 사이트에서 정확히 찾기 어려운 장면은 stock_image로 분류하지 않는다. "
        f"실제 원본이 필요 없으면 generated_image/video, 실제 외형이 중요하면 real_image 또는 "
        f"manual_required를 선택한다. 단, 서울 전경·경복궁·한강처럼 널리 촬영된 명소와 일반 풍경은 "
        f"검색 가능성이 높으면 stock_image를 사용할 수 있다.\n"
        f"- screenshot/meme: 원본 화면 캡처 또는 사람이 맥락에 맞게 고를 밈.\n"
        f"- manual_required: 원본 영상 등 위 유형으로 처리할 수 없고 사람이 직접 확보해야 하는 경우에만 사용한다.\n"
        f"- fact_sensitive=true: 실제 제품·포장·로고·특정 실존 인물·문서처럼 원본과 동일해야 할 때만 사용한다. "
        f"가상 재현 배우나 '협상 장면을 연상시키는 인물'에는 false다.\n"
        f"- 예: '일본 바이어 미팅 또는 협상 장면 연상 인물 사진'은 "
        f"generated_image / fact_sensitive=false다.\n"
        f"- 예: '실제 대표 인물 사진'은 real_image / fact_sensitive=true다.\n"
        f"- asset_reason_suggestions에는 선택 근거를 한 문장으로 쓴다.\n"
        f"- stock_query_suggestions는 stock_image 컷마다 Openverse·Pixabay·Pexels에서 검색할 "
        f"간결한 영어 명사구(2~6개 핵심 단어)로 쓴다. Korea/Seoul 같은 지역 맥락과 고유명사는 "
        f"필요할 때 유지하고, 문장·연출 지시·자막 문구는 제외한다. stock_image가 아닌 컷은 빈 문자열로 쓴다.\n"
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
        asset_types = rec.get("asset_type_suggestions")
        fact_flags = rec.get("fact_sensitive_suggestions")
        reasons = rec.get("asset_reason_suggestions")
        stock_queries = rec.get("stock_query_suggestions")
        if (
            isinstance(asset_types, list)
            and len(asset_types) == n
            and all(str(item) in _ASSET_TYPES for item in asset_types)
            and isinstance(fact_flags, list)
            and len(fact_flags) == n
            and all(isinstance(item, bool) for item in fact_flags)
        ):
            seg["asset_type_suggestions"] = [
                str(item) for item in asset_types
            ]
            seg["fact_sensitive_suggestions"] = list(fact_flags)
            seg["asset_reason_suggestions"] = _fit(
                reasons if isinstance(reasons, list) else [],
                n,
                "생성 모델이 컷의 문맥과 확보 가능성을 기준으로 분류했습니다.",
            )
            if (
                isinstance(stock_queries, list)
                and len(stock_queries) == n
                and all(isinstance(item, str) for item in stock_queries)
            ):
                seg["stock_query_suggestions"] = [
                    str(item).strip() for item in stock_queries
                ]
            seg["asset_classification_source"] = "generation_llm"
    return guide
