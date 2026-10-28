"""Convert the established guide dictionary into a versioned cut manifest."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

from ..domain.assets import (
    ApprovalStatus,
    AssetType,
    CutManifest,
    CutSpec,
    MediaKind,
)

ASSET_CLASSIFIER_VERSION = 6

AI_PERSON_IMAGE_DISCLOSURE = (
    "이 이미지는 이해를 돕기 위한 AI 생성\n"
    "이미지이며, 실존 인물이 아닙니다."
)
AI_PERSON_VIDEO_DISCLOSURE = (
    "이 영상은 이해를 돕기 위한 AI 생성\n"
    "영상이며, 실존 인물이 아닙니다."
)


def _guide_cut_count(segment: Mapping[str, Any]) -> int:
    sentence_count = max(1, int(segment.get("sentence_count") or 1))
    sentence_cap = min(sentence_count * 3, 18)
    return max(1, min(int(segment.get("cuts_suggested") or 1), sentence_cap))


def _cut_ratio_edges(
    segment: Mapping[str, Any],
    cut_count: int,
) -> list[float]:
    raw = segment.get("cut_change_ratios")
    if isinstance(raw, list) and len(raw) == cut_count - 1:
        try:
            changes = [float(value) for value in raw]
        except (TypeError, ValueError):
            changes = []
        if (
            len(changes) == cut_count - 1
            and all(0.0 < value < 1.0 for value in changes)
            and changes == sorted(changes)
        ):
            return [0.0, *changes, 1.0]
    return [index / cut_count for index in range(cut_count + 1)]

_FACT_TERMS = (
    "실제",
    "실물",
    "제품",
    "인물",
    "사건",
    "뉴스",
    "통계",
    "증거",
    "정확",
    "공식",
    "보도",
    "기사",
    "인터뷰",
    "발언",
    "로고",
    "브랜드",
    "포장지",
    "회사 전경",
    "fact",
    "real person",
    "product",
)
_SCREENSHOT_TERMS = ("스크린샷", "화면 캡처", "screen shot", "screenshot")
_MEME_TERMS = ("밈", "meme", "짤방", "반응 짤")
_GENERATED_TERMS = (
    "ai 생성",
    "생성",
    "일러스트",
    "상상",
    "합성",
    "상징",
    "암시",
    "의인화",
    "캐릭터",
    "초현실",
    "비현실",
    "과장",
    "판타지",
    "그래픽",
    "인포그래픽",
    "애니메이션",
    "만화",
    "3d",
    "concept",
    "illustration",
)
_REAL_TERMS = ("실사", "실제 사진", "원본 사진", "real photo")
_VIDEO_TERMS = ("영상", "비디오", "움짤", "video", "motion", "clip")
_PHOTO_TERMS = ("사진", "포토", "photo")
_STOCK_TERMS = (
    "스톡",
    "자료 사진",
    "자료사진",
    "아카이브",
    "배경 사진",
    "도시 전경",
    "거리 풍경",
    "자연 풍경",
    "시장 전경",
    "매장 전경",
    "건물 외관",
    "일상 장면",
    "b-roll",
    "b roll",
    "stock",
    "archive",
)
_RECREATION_TERMS = (
    "연상",
    "재현",
    "상황극",
    "가상의",
    "가상 인물",
    "모의",
    "드라마타이즈",
    "reenact",
    "recreation",
)
_EXACT_SOURCE_TERMS = (
    "실제",
    "실물",
    "원본",
    "제품",
    "로고",
    "브랜드",
    "포장지",
    "공식",
)
_PRODUCT_IDENTITY_SUPPRESSION_TERMS = (
    "무지 용기",
    "무지 흰색",
    "무지 컵",
    "무지 포장",
    "로고 없음",
    "로고·문자 없음",
    "글자·로고 없음",
    "브랜드 없음",
    "브랜드 외형 없이",
    "실제 브랜드 외형은 쓰지 않음",
    "실제 브랜드 외형을 쓰지 않음",
    "실제 브랜드 외형 없이",
    "generic packaging",
    "blank packaging",
    "unbranded",
)
_PRODUCT_VISUAL_TERMS = (
    "제품",
    "포장",
    "포장지",
    "컵라면",
    "용기면",
    "라면 용기",
    "사각 용기",
    "과자",
    "음료병",
    "제품 병",
    "제품 캔",
    "제품 상자",
)
_KOREAN_LOCAL_CONTEXT_TERMS = (
    "한국인",
    "한국 회사",
    "한국 기업",
    "한국 사무실",
    "한국 가정",
    "한국 아파트",
    "한국 학교",
    "한국 교실",
    "한국 편의점",
    "한국 마트",
    "한국 슈퍼",
    "한국 시장",
    "전통시장",
    "한국 식당",
    "한국 카페",
    "한복",
    "한옥",
    "김치",
    "떡",
    "Korean office",
    "Korean home",
    "Korean school",
    "Korean convenience store",
    "Korean supermarket",
)
_COMMON_KOREAN_STOCK_TERMS = (
    "서울 전경",
    "서울 야경",
    "부산 전경",
    "제주 풍경",
    "한강",
    "경복궁",
    "광화문",
    "남산타워",
    "태극기",
    "Seoul skyline",
    "Han River",
    "Gyeongbokgung",
)
_EXACT_RENDER_TERMS = (
    "그래프",
    "차트",
    "도표",
    "표 그래픽",
    "인포그래픽",
    "정보 카드",
    "정보카드",
    "수치 카드",
    "수치 강조",
    "통계 카드",
    "데이터 시각화",
    "발언 자막 카드",
    "발언 카드",
    "명언 카드",
    "뉴스 화면",
    "기사 화면",
    "문서 화면",
    "graph",
    "chart",
    "infographic",
    "data card",
)
_GEOGRAPHIC_RENDER_TERMS = (
    "한반도 지도",
    "한국 지도",
    "일본 지도",
    "세계 지도",
    "독도",
    "국경",
    "영토",
    "지형도",
    "map of korea",
    "map of japan",
    "world map",
)
_NONFACTUAL_MAP_TERMS = (
    "가상 지도",
    "판타지 지도",
    "캐릭터",
    "의인화",
    "과장된",
    "비현실",
    "상상",
)
_PRIVATE_PERSON_TERMS = (
    "대표 인물",
    "회사 대표",
    "기업 대표",
    "점주",
    "창업자",
    "인터뷰이",
    "관계자",
    "일반인",
)
_PUBLIC_FIGURE_TERMS = (
    "대통령",
    "총리",
    "국회의원",
    "연예인",
    "배우",
    "가수",
    "아이돌",
    "유명인",
    "대기업 총수",
    "이재용",
    "일론 머스크",
    "트럼프",
)


def _pick(items: Any, index: int) -> str:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not values:
        return ""
    return values[index] if index < len(values) else values[-1]


def _pick_exact(items: Any, index: int) -> Any:
    if not isinstance(items, (list, tuple)) or index >= len(items):
        return None
    return items[index]


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _stock_search_is_unreliable(text: str) -> bool:
    return _contains(text, _KOREAN_LOCAL_CONTEXT_TERMS) and not _contains(
        text,
        _COMMON_KOREAN_STOCK_TERMS,
    )


def generated_visual_requires_exact_source(text: str) -> bool:
    """True when a generative model could invent factual visual information."""

    return _contains(text, _EXACT_RENDER_TERMS) or (
        _contains(text, _GEOGRAPHIC_RENDER_TERMS)
        and not _contains(text, _NONFACTUAL_MAP_TERMS)
    )


def _looks_like_private_person(text: str) -> bool:
    return _contains(text, _PRIVATE_PERSON_TERMS) and not _contains(
        text,
        _PUBLIC_FIGURE_TERMS,
    )


def _fictional_person_prompt() -> str:
    return (
        "실존 인물과 닮지 않은 가상의 한국인 기업 대표가 단호하고 "
        "당당하게 말하는 자연스러운 재연 장면, 실제 인물의 얼굴·외형 "
        "복제와 이미지 내 글자 금지"
    )


def _generated_product_identity_is_suppressed(
    *,
    prompt: str,
    narration: str,
    reason: str = "",
) -> bool:
    """Detect generated stand-ins that erase a product central to the story."""

    production_context = " ".join((prompt, reason))
    story_context = " ".join((prompt, narration, reason))
    return (
        _contains(production_context, _PRODUCT_IDENTITY_SUPPRESSION_TERMS)
        and _contains(story_context, _PRODUCT_VISUAL_TERMS)
    )


def classify_asset_type(
    *,
    prompt: str,
    media_hint: str,
    narration: str,
    fact_sensitive: Optional[bool] = None,
) -> tuple[AssetType, MediaKind, bool, list[str]]:
    """Choose an AI-first default while preserving factual-source boundaries."""

    combined = " ".join((prompt, media_hint, narration))
    visual_context = " ".join((prompt, media_hint)).strip()
    is_fact_sensitive = (
        _contains(visual_context or narration, _FACT_TERMS)
        if fact_sensitive is None
        else bool(fact_sensitive)
    )
    if (
        fact_sensitive is None
        and _contains(prompt, _RECREATION_TERMS)
        and not _contains(prompt, _EXACT_SOURCE_TERMS)
    ):
        is_fact_sensitive = False
    notes: list[str] = []
    if generated_visual_requires_exact_source(prompt):
        notes.append(
            "지도·수치·그래프·정보 카드는 생성형 AI가 사실과 다른 내용을 "
            "만들 수 있어 원본 자료 또는 사람이 만든 정확한 화면이 필요합니다."
        )
        return AssetType.MANUAL_REQUIRED, MediaKind.IMAGE, True, notes
    if _contains(combined, _SCREENSHOT_TERMS):
        notes.append("원본 화면을 직접 캡처해야 하는 컷입니다.")
        return AssetType.SCREENSHOT, MediaKind.IMAGE, is_fact_sensitive, notes
    if _contains(combined, _MEME_TERMS):
        notes.append("맥락에 맞는 밈·짤을 사람이 선택하는 컷입니다.")
        return AssetType.MEME, MediaKind.IMAGE, is_fact_sensitive, notes
    if is_fact_sensitive:
        if _contains(media_hint, _VIDEO_TERMS):
            notes.append(
                "사실성이 중요한 영상은 원본·라이선스 영상을 직접 확보해야 합니다."
            )
            return AssetType.MANUAL_REQUIRED, MediaKind.VIDEO, True, notes
        if _contains(prompt, _PHOTO_TERMS + _REAL_TERMS):
            notes.append(
                "실제 제품·인물·사건을 보여 주므로 원본 사진을 직접 확보해야 합니다."
            )
            return AssetType.REAL_IMAGE, MediaKind.IMAGE, True, notes
        notes.append(
            "사실성이 중요한 컷이라 AI 생성 대신 검증된 원본을 직접 넣어야 합니다."
        )
        return AssetType.MANUAL_REQUIRED, MediaKind.IMAGE, True, notes
    if _contains(media_hint, _VIDEO_TERMS):
        if _contains(combined, _STOCK_TERMS):
            notes.append(
                "일반 자료 영상으로 확보 가능한 컷이지만 스톡 영상 자동 검색은 아직 지원하지 않습니다."
            )
            return AssetType.MANUAL_REQUIRED, MediaKind.VIDEO, False, notes
        notes.append(
            "스톡으로 찾기 어려운 연출 컷이라 AI 영상 생성을 기본 추천합니다."
        )
        return AssetType.GENERATED_VIDEO, MediaKind.VIDEO, False, notes
    if _contains(combined, _REAL_TERMS):
        notes.append("원본 또는 직접 촬영한 실제 이미지를 사용하는 컷입니다.")
        return AssetType.REAL_IMAGE, MediaKind.IMAGE, False, notes
    if _contains(combined, _STOCK_TERMS) and not _stock_search_is_unreliable(
        visual_context
    ):
        notes.append(
            "일반 자료화면으로 충분히 확보 가능한 컷이라 스톡 이미지 검색을 추천합니다."
        )
        return AssetType.STOCK_IMAGE, MediaKind.IMAGE, False, notes
    notes.append(
        "사실성 제약이 없고 스톡으로 정확히 찾기 어려운 컷이라 AI 이미지 생성을 기본 추천합니다."
    )
    return AssetType.GENERATED_IMAGE, MediaKind.IMAGE, False, notes


def build_cut_manifest(
    guide: Mapping[str, Any],
    *,
    overrides: Optional[Mapping[str, str]] = None,
) -> CutManifest:
    """Build cuts without mutating the existing guide contract."""

    canonical = json.dumps(
        guide,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    cuts: list[CutSpec] = []
    overrides = overrides or {}
    for segment_position, segment in enumerate(guide.get("segments") or []):
        segment_index = int(segment.get("index", segment_position))
        cut_count = _guide_cut_count(segment)
        cut_edges = _cut_ratio_edges(segment, cut_count)
        start_sec = float(segment.get("start_sec") or 0.0)
        duration_sec = max(float(segment.get("duration_sec") or 0.0), 0.0)
        for cut_index in range(cut_count):
            cut_id = f"s{segment_index:03d}-c{cut_index:02d}"
            prompt = _pick(segment.get("image_suggestions"), cut_index)
            media_hint = _pick(segment.get("media_suggestions"), cut_index)
            model_asset_type = _pick_exact(
                segment.get("asset_type_suggestions"),
                cut_index,
            )
            model_fact_sensitive = _pick_exact(
                segment.get("fact_sensitive_suggestions"),
                cut_index,
            )
            model_reason = _pick_exact(
                segment.get("asset_reason_suggestions"),
                cut_index,
            )
            stock_query = str(
                _pick_exact(
                    segment.get("stock_query_suggestions"),
                    cut_index,
                )
                or ""
            ).strip()
            person_depiction = str(
                _pick_exact(
                    segment.get("person_depiction_suggestions"),
                    cut_index,
                )
                or "none"
            ).strip()
            disclosure_requested = bool(
                _pick_exact(
                    segment.get("ai_disclosure_suggestions"),
                    cut_index,
                )
            )
            try:
                recommended_type = AssetType(model_asset_type)
            except (TypeError, ValueError):
                recommended_type = None
            if (
                recommended_type is not None
                and isinstance(model_fact_sensitive, bool)
            ):
                fact_sensitive = model_fact_sensitive
                asset_type = recommended_type
                media_kind = (
                    MediaKind.VIDEO
                    if asset_type is AssetType.GENERATED_VIDEO
                    or (
                        asset_type is AssetType.MANUAL_REQUIRED
                        and _contains(media_hint, _VIDEO_TERMS)
                    )
                    else MediaKind.IMAGE
                )
                notes = [
                    "생성 모델 판단: "
                    + (
                        str(model_reason).strip()
                        if model_reason
                        else "컷 문맥과 에셋 확보 가능성을 기준으로 분류했습니다."
                    )
                ]
                if (
                    asset_type
                    in (
                        AssetType.REAL_IMAGE,
                        AssetType.STOCK_IMAGE,
                        AssetType.GENERATED_IMAGE,
                        AssetType.GENERATED_VIDEO,
                    )
                    and (
                        person_depiction == "private_person"
                        or (
                            person_depiction == "none"
                            and _looks_like_private_person(prompt)
                        )
                    )
                ):
                    prompt = _fictional_person_prompt()
                    if asset_type is AssetType.GENERATED_VIDEO:
                        media_kind = MediaKind.VIDEO
                    else:
                        asset_type = AssetType.GENERATED_IMAGE
                        media_kind = MediaKind.IMAGE
                    fact_sensitive = False
                    person_depiction = "fictional_person"
                    disclosure_requested = True
                    notes.extend(
                        [
                            "비공인 실존 인물의 원본 사진 대신 특정 인물과 닮지 않은 "
                            "가상 인물 재연으로 보정했습니다."
                        ]
                    )
                if (
                    asset_type
                    in (
                        AssetType.GENERATED_IMAGE,
                        AssetType.GENERATED_VIDEO,
                    )
                    and generated_visual_requires_exact_source(prompt)
                ):
                    asset_type = AssetType.MANUAL_REQUIRED
                    media_kind = MediaKind.IMAGE
                    fact_sensitive = True
                    disclosure_requested = False
                    notes.extend(
                        [
                            "지도·수치·그래프·정보 카드는 생성 결과의 환각 위험 때문에 "
                            "자동 생성을 차단했습니다."
                        ]
                    )
                if (
                    asset_type is AssetType.STOCK_IMAGE
                    and _stock_search_is_unreliable(prompt)
                ):
                    asset_type = AssetType.GENERATED_IMAGE
                    media_kind = MediaKind.IMAGE
                    stock_query = ""
                    notes.extend(
                        [
                            "한국 고유 디테일이 핵심인 장면은 일반 스톡 검색의 정확도가 낮아 "
                            "AI 이미지 생성으로 보정했습니다."
                        ]
                    )
                exact_source_required = (
                    _contains(prompt, _EXACT_SOURCE_TERMS)
                    and not _contains(
                        prompt,
                        _RECREATION_TERMS + _GENERATED_TERMS,
                    )
                )
                if (
                    asset_type
                    in (
                        AssetType.GENERATED_IMAGE,
                        AssetType.GENERATED_VIDEO,
                    )
                    and (fact_sensitive or exact_source_required)
                ):
                    asset_type, media_kind, fact_sensitive, fallback_notes = (
                        classify_asset_type(
                            prompt=prompt,
                            media_hint=media_hint,
                            narration=str(segment.get("narration") or ""),
                            fact_sensitive=True,
                        )
                    )
                    notes.extend(
                        [
                            "생성 모델의 사실성 판정과 생성 유형이 충돌해 안전 규칙으로 차단했습니다.",
                            *fallback_notes,
                        ]
                    )
            else:
                asset_type, media_kind, fact_sensitive, notes = (
                    classify_asset_type(
                        prompt=prompt,
                        media_hint=media_hint,
                        narration=str(segment.get("narration") or ""),
                        fact_sensitive=segment.get("fact_sensitive"),
                    )
                )
            if (
                asset_type
                in (
                    AssetType.GENERATED_IMAGE,
                    AssetType.GENERATED_VIDEO,
                    AssetType.MANUAL_REQUIRED,
                )
                and _generated_product_identity_is_suppressed(
                    prompt=prompt,
                    narration=str(segment.get("narration") or ""),
                    reason=str(model_reason or ""),
                )
            ):
                original_media_kind = media_kind
                asset_type = AssetType.MANUAL_REQUIRED
                media_kind = (
                    MediaKind.VIDEO
                    if original_media_kind is MediaKind.VIDEO
                    or _contains(media_hint, _VIDEO_TERMS)
                    else MediaKind.IMAGE
                )
                fact_sensitive = True
                disclosure_requested = False
                stock_query = ""
                notes.extend(
                    [
                        "이야기의 실제 제품을 무지·무브랜드 대체품으로 바꾸는 "
                        "자동 생성을 차단했습니다.",
                        "실제 제품 사진을 참조로 제공해 포장과 용기 외형을 "
                        "보존하는 수동 생성 또는 원본 삽입이 필요합니다.",
                    ]
                )
            if cut_id in overrides:
                requested_type = AssetType(overrides[cut_id])
                if fact_sensitive and requested_type in (
                    AssetType.GENERATED_IMAGE,
                    AssetType.GENERATED_VIDEO,
                ):
                    notes.append(
                        "사실성 보호 대상이라 생성형 에셋 유형 변경을 적용하지 않았습니다."
                    )
                else:
                    asset_type = requested_type
                    media_kind = (
                        MediaKind.VIDEO
                        if asset_type is AssetType.GENERATED_VIDEO
                        else MediaKind.IMAGE
                    )
                    notes.append("사용자가 에셋 유형을 변경했습니다.")
            disclosure_text = ""
            if (
                disclosure_requested
                and person_depiction == "fictional_person"
                and asset_type
                in (
                    AssetType.GENERATED_IMAGE,
                    AssetType.GENERATED_VIDEO,
                )
            ):
                disclosure_text = (
                    AI_PERSON_VIDEO_DISCLOSURE
                    if asset_type is AssetType.GENERATED_VIDEO
                    else AI_PERSON_IMAGE_DISCLOSURE
                )
            paid = asset_type in (
                AssetType.GENERATED_IMAGE,
                AssetType.GENERATED_VIDEO,
            )
            cut_start = start_sec + duration_sec * cut_edges[cut_index]
            cut_end = start_sec + duration_sec * cut_edges[cut_index + 1]
            cuts.append(
                CutSpec(
                    id=cut_id,
                    segment_index=segment_index,
                    cut_index=cut_index,
                    start_sec=round(cut_start, 4),
                    duration_sec=round(max(0.0, cut_end - cut_start), 4),
                    narration=str(segment.get("narration") or ""),
                    caption=str(segment.get("caption") or ""),
                    prompt=prompt or str(segment.get("intent") or ""),
                    asset_type=asset_type,
                    media_kind=media_kind,
                    stock_query=(
                        stock_query
                        if asset_type is AssetType.STOCK_IMAGE
                        else ""
                    ),
                    person_depiction=person_depiction,
                    disclosure_text=disclosure_text,
                    fact_sensitive=fact_sensitive,
                    approval_status=(
                        ApprovalStatus.PENDING
                        if paid
                        else ApprovalStatus.NOT_REQUIRED
                    ),
                    notes=notes,
                )
            )
    return CutManifest(
        title=str(guide.get("title") or ""),
        cuts=cuts,
        source_guide_hash=hashlib.sha256(canonical).hexdigest(),
        metadata={
            "source_schema": "legacy-guide-dict",
            "asset_classifier_version": ASSET_CLASSIFIER_VERSION,
            "total_duration_sec": float(
                guide.get("total_duration_sec") or 0.0
            ),
        },
    )
