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

ASSET_CLASSIFIER_VERSION = 3

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
    if _contains(combined, _STOCK_TERMS):
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
        cut_count = max(1, min(int(segment.get("cuts_suggested") or 1), 6))
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
            if cut_id in overrides:
                asset_type = AssetType(overrides[cut_id])
                media_kind = (
                    MediaKind.VIDEO
                    if asset_type is AssetType.GENERATED_VIDEO
                    else MediaKind.IMAGE
                )
                notes.append("사용자가 에셋 유형을 변경했습니다.")
            paid = asset_type in (
                AssetType.GENERATED_IMAGE,
                AssetType.GENERATED_VIDEO,
            )
            cut_start = start_sec + duration_sec * cut_index / cut_count
            cut_end = start_sec + duration_sec * (cut_index + 1) / cut_count
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
