"""Versioned cut and asset contracts shared by product compositions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional
from uuid import uuid4


class AssetType(str, Enum):
    REAL_IMAGE = "real_image"
    STOCK_IMAGE = "stock_image"
    GENERATED_IMAGE = "generated_image"
    GENERATED_VIDEO = "generated_video"
    MEME = "meme"
    SCREENSHOT = "screenshot"
    MANUAL_REQUIRED = "manual_required"


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CandidateStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    REJECTED = "rejected"
    SELECTED = "selected"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AssetCandidate:
    id: str = field(default_factory=lambda: uuid4().hex)
    provider: str = ""
    model: str = ""
    media_kind: MediaKind = MediaKind.IMAGE
    status: CandidateStatus = CandidateStatus.PENDING
    original_path: Optional[str] = None
    prepared_path: Optional[str] = None
    mime_type: Optional[str] = None
    external_id: Optional[str] = None
    cost_usd: float = 0.0
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["media_kind"] = self.media_kind.value
        value["status"] = self.status.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AssetCandidate":
        return cls(
            id=str(value.get("id") or uuid4().hex),
            provider=str(value.get("provider") or ""),
            model=str(value.get("model") or ""),
            media_kind=MediaKind(value.get("media_kind") or MediaKind.IMAGE.value),
            status=CandidateStatus(
                value.get("status") or CandidateStatus.PENDING.value
            ),
            original_path=value.get("original_path"),
            prepared_path=value.get("prepared_path"),
            mime_type=value.get("mime_type"),
            external_id=value.get("external_id"),
            cost_usd=float(value.get("cost_usd") or 0.0),
            error=value.get("error"),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class CutSpec:
    id: str
    segment_index: int
    cut_index: int
    start_sec: float
    duration_sec: float
    narration: str
    caption: str
    prompt: str
    asset_type: AssetType
    media_kind: MediaKind
    stock_query: str = ""
    person_depiction: str = "none"
    disclosure_text: str = ""
    fact_sensitive: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    candidates: list[AssetCandidate] = field(default_factory=list)
    selected_candidate_id: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def selected_candidate(self) -> Optional[AssetCandidate]:
        return next(
            (
                candidate
                for candidate in self.candidates
                if candidate.id == self.selected_candidate_id
            ),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["asset_type"] = self.asset_type.value
        value["media_kind"] = self.media_kind.value
        value["approval_status"] = self.approval_status.value
        value["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CutSpec":
        return cls(
            id=str(value["id"]),
            segment_index=int(value.get("segment_index") or 0),
            cut_index=int(value.get("cut_index") or 0),
            start_sec=float(value.get("start_sec") or 0.0),
            duration_sec=float(value.get("duration_sec") or 0.0),
            narration=str(value.get("narration") or ""),
            caption=str(value.get("caption") or ""),
            prompt=str(value.get("prompt") or ""),
            asset_type=AssetType(
                value.get("asset_type") or AssetType.MANUAL_REQUIRED.value
            ),
            media_kind=MediaKind(
                value.get("media_kind") or MediaKind.IMAGE.value
            ),
            stock_query=str(value.get("stock_query") or ""),
            person_depiction=str(
                value.get("person_depiction") or "none"
            ),
            disclosure_text=str(value.get("disclosure_text") or ""),
            fact_sensitive=bool(value.get("fact_sensitive", False)),
            approval_status=ApprovalStatus(
                value.get("approval_status")
                or ApprovalStatus.NOT_REQUIRED.value
            ),
            candidates=[
                AssetCandidate.from_dict(item)
                for item in value.get("candidates") or []
            ],
            selected_candidate_id=value.get("selected_candidate_id"),
            notes=[str(item) for item in value.get("notes") or []],
        )


@dataclass
class CutManifest:
    title: str
    cuts: list[CutSpec]
    manifest_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: str = "shortly-cut-manifest-v1"
    created_at: str = field(default_factory=_utc_now)
    source_guide_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def cut(self, cut_id: str) -> CutSpec:
        for item in self.cuts:
            if item.id == cut_id:
                return item
        raise KeyError(f"Unknown cut id: {cut_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "title": self.title,
            "source_guide_hash": self.source_guide_hash,
            "metadata": dict(self.metadata),
            "cuts": [cut.to_dict() for cut in self.cuts],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CutManifest":
        return cls(
            schema_version=str(
                value.get("schema_version") or "shortly-cut-manifest-v1"
            ),
            manifest_id=str(value.get("manifest_id") or uuid4().hex),
            created_at=str(value.get("created_at") or _utc_now()),
            title=str(value.get("title") or ""),
            source_guide_hash=str(value.get("source_guide_hash") or ""),
            metadata=dict(value.get("metadata") or {}),
            cuts=[CutSpec.from_dict(item) for item in value.get("cuts") or []],
        )
