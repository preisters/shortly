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
_PERSON_DEPICTIONS = {
    "none",
    "public_figure",
    "private_person",
    "fictional_person",
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
            f"'{s.get('intent', '')}' / 나레이션 \"{(s.get('narration') or '')[:180]}\""
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
        '"stock_query_suggestions": string[], '
        '"person_depiction_suggestions": '
        '("none"|"public_figure"|"private_person"|"fictional_person")[], '
        '"ai_disclosure_suggestions": boolean[]}]} '
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
        f"[쇼츠 화면 연출 원칙]\n"
        f"- 모든 세그먼트를 하나의 연속 스토리보드로 본다. 훅은 즉시 이해되는 이상한 상황, "
        f"정체 공개는 실제 대상, 갈등은 인물의 행동·표정, 선택은 구체적 동작, 결말은 결과가 "
        f"보이는 장면으로 시각적 역할을 바꾼다. 각 컷은 앞 컷과 다른 새 정보를 보여줘야 한다.\n"
        f"- 같은 제품 정면 사진·같은 인물 구도·같은 회의 장면을 연속 두 컷 넘게 반복하지 않는다. "
        f"제품 클로즈업은 정체 공개와 필요한 마무리에 우선 사용하고, 중간에는 손동작·공간·반응·"
        f"물류·소비 장면처럼 샷 크기와 행동을 바꾼다.\n"
        f"- 생성 영상은 이동·제조·운반·사람의 반응처럼 실제 움직임이 정보나 감정을 강화할 때만 "
        f"추천한다. 정적인 제품·문서·수치 근거는 영상으로 억지 변환하지 않는다.\n"
        f"- 기본 화면은 인물의 표정·행동, 실제 제품 디테일, 공간, 손동작, 상황 재연처럼 "
        f"즉시 이해되는 한 장면이다. 카드뉴스·정보 카드·발언 카드·포스터·UI 형태를 기본값으로 쓰지 않는다.\n"
        f"- 발언을 강조할 때는 화자의 감정을 보여 주는 인물/상황 장면을 쓰고, 정확한 발언 문구는 "
        f"기존 CapCut 자막이 담당한다. 생성 이미지 안에 인용문이나 자막을 넣지 않는다.\n"
        f"- 생성형 AI에 그래프·차트·표·수치 카드·뉴스 화면·문서·지도·국경·영토 위치를 그리게 하지 않는다. "
        f"수치와 문장은 CapCut 자막으로 표시하고, 화면은 실제 기사 캡처나 관련 실사 장면으로 대체한다. "
        f"지도·제품 표기처럼 위치와 외형이 정확해야 하면 원본 이미지/스크린샷/수동 확보를 선택한다.\n\n"
        f"[에셋 분류 원칙]\n"
        f"- generated_image/video: 실제 기록 화면이 존재하지 않는 재현·연상·상황극·상징·합성·과장 연출. "
        f"사실을 설명하는 대본이어도 화면이 재현 장면이면 생성형을 선택한다.\n"
        f"- real_image: 실제 제품, 포장, 로고 또는 얼굴 자체가 이야기의 핵심인 널리 알려진 공인처럼 "
        f"외형이 정확해야 하고 사용 가능한 원본 사진을 확보할 가치가 큰 경우.\n"
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
        f"- person_depiction: 인물이 없으면 none. 대중이 얼굴을 알아보는 연예인·고위 공직자·전국적으로 "
        f"잘 알려진 대기업 총수 등 얼굴 식별이 서사에 중요한 경우만 public_figure다. 일반 회사 대표·점주·"
        f"창업자·인터뷰이는 이름이 기사에 나와도 private_person으로 본다. 실존 인물과 닮지 않은 재연 배우는 "
        f"fictional_person이다.\n"
        f"- private_person은 초상·인터뷰 원본 사진을 기본 추천하지 않는다. 인물의 감정·행동이 장면에 도움이 "
        f"되면 특정 실존 인물과 닮지 않은 fictional_person의 generated_image/video로 바꾸고, 필요 없으면 "
        f"제품·공간·손동작 등 다른 장면으로 교체한다. 이재용처럼 얼굴 자체가 대중적 맥락인 경우는 "
        f"public_figure 예외가 될 수 있다.\n"
        f"- ai_disclosure=true는 가상의 인물로 실존 인물의 역할·상황을 재연하는 생성 이미지/영상에만 쓴다. "
        f"단순 사물·배경 생성에는 false다.\n"
        f"- 예: '일본 바이어 미팅 또는 협상 장면 연상 인물 사진'은 "
        f"generated_image / fact_sensitive=false다.\n"
        f"- 예: 이름이 덜 알려진 회사 대표의 발언 장면은 특정 얼굴을 복제하지 않은 가상 대표가 단호하게 "
        f"말하는 generated_image / fictional_person / fact_sensitive=false / ai_disclosure=true다. "
        f"발언 문구는 이미지가 아니라 CapCut 자막으로 보여준다.\n"
        f"- asset_reason_suggestions에는 선택 근거를 한 문장으로 쓴다.\n"
        f"- stock_query_suggestions는 stock_image 컷마다 Openverse·Pixabay·Pexels에서 검색할 "
        f"간결한 영어 명사구(2~6개 핵심 단어)로 쓴다. Korea/Seoul 같은 지역 맥락과 고유명사는 "
        f"필요할 때 유지하고, 문장·연출 지시·자막 문구는 제외한다. stock_image가 아닌 컷은 빈 문자열로 쓴다.\n"
        f"스키마: {schema}"
    )
    data = llm.complete_json(system, user, max_tokens=6000)
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
        person_depictions = rec.get("person_depiction_suggestions")
        disclosures = rec.get("ai_disclosure_suggestions")
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
            if (
                isinstance(person_depictions, list)
                and len(person_depictions) == n
                and all(
                    str(item) in _PERSON_DEPICTIONS
                    for item in person_depictions
                )
            ):
                normalized_people = [
                    str(item) for item in person_depictions
                ]
                normalized_disclosures = (
                    list(disclosures)
                    if (
                        isinstance(disclosures, list)
                        and len(disclosures) == n
                        and all(
                            isinstance(item, bool)
                            for item in disclosures
                        )
                    )
                    else [False] * n
                )
                for index, depiction in enumerate(normalized_people):
                    if (
                        depiction == "private_person"
                        and seg["asset_type_suggestions"][index]
                        in ("real_image", "generated_image")
                    ):
                        normalized_people[index] = "fictional_person"
                        seg["asset_type_suggestions"][index] = (
                            "generated_image"
                        )
                        seg["fact_sensitive_suggestions"][index] = False
                        seg["image_suggestions"][index] = (
                            "실존 인물과 닮지 않은 가상의 한국인 기업 대표가 "
                            "단호하고 당당하게 말하는 자연스러운 재연 장면, "
                            "실제 인물의 얼굴·외형 복제와 이미지 내 글자 금지"
                        )
                        normalized_disclosures[index] = True
                    if (
                        normalized_people[index] == "fictional_person"
                        and seg["asset_type_suggestions"][index]
                        in ("generated_image", "generated_video")
                    ):
                        normalized_disclosures[index] = True
                seg["person_depiction_suggestions"] = normalized_people
                seg["ai_disclosure_suggestions"] = normalized_disclosures
            seg["asset_classification_source"] = "generation_llm"
    return guide
