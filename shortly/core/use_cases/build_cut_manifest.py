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
    "fact",
    "real person",
    "product",
)
_SCREENSHOT_TERMS = ("스크린샷", "화면 캡처", "screen shot", "screenshot")
_MEME_TERMS = ("밈", "meme", "짤")
_GENERATED_TERMS = ("생성", "일러스트", "상상", "concept", "illustration")
_REAL_TERMS = ("실사", "실제 사진", "원본 사진", "real photo")
_VIDEO_TERMS = ("영상", "비디오", "움짤", "video", "motion", "clip")


def _pick(items: Any, index: int) -> str:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not values:
        return ""
    return values[index] if index < len(values) else values[-1]


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
    """Apply conservative deterministic rules before any optional LLM review."""

    combined = " ".join((prompt, media_hint, narration))
    is_fact_sensitive = (
        _contains(combined, _FACT_TERMS)
        if fact_sensitive is None
        else bool(fact_sensitive)
    )
    notes: list[str] = []
    if is_fact_sensitive:
        notes.append("사실성이 중요한 컷은 자동 생성하지 않습니다.")
        return AssetType.MANUAL_REQUIRED, MediaKind.IMAGE, True, notes
    if _contains(combined, _SCREENSHOT_TERMS):
        return AssetType.SCREENSHOT, MediaKind.IMAGE, False, notes
    if _contains(combined, _MEME_TERMS):
        return AssetType.MEME, MediaKind.IMAGE, False, notes
    if _contains(media_hint, _VIDEO_TERMS):
        if _contains(combined, _GENERATED_TERMS):
            return AssetType.GENERATED_VIDEO, MediaKind.VIDEO, False, notes
        notes.append("기존 영상 검색 또는 수동 삽입이 필요합니다.")
        return AssetType.MANUAL_REQUIRED, MediaKind.VIDEO, False, notes
    if _contains(combined, _REAL_TERMS):
        return AssetType.REAL_IMAGE, MediaKind.IMAGE, False, notes
    if _contains(combined, _GENERATED_TERMS):
        return AssetType.GENERATED_IMAGE, MediaKind.IMAGE, False, notes
    return AssetType.STOCK_IMAGE, MediaKind.IMAGE, False, notes


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
            asset_type, media_kind, fact_sensitive, notes = classify_asset_type(
                prompt=prompt,
                media_hint=media_hint,
                narration=str(segment.get("narration") or ""),
                fact_sensitive=segment.get("fact_sensitive"),
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
            "total_duration_sec": float(
                guide.get("total_duration_sec") or 0.0
            ),
        },
    )
