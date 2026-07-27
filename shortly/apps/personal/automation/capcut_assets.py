"""Two-phase validation and commit for selected assets in CapCut exports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from ....core.domain.assets import AssetType
from ....generate.capcut_export import (
    _pick_templates,
    export_to_capcut_project,
    find_capcut_draft_root,
)
from .store import AutomationStore


@dataclass(frozen=True)
class PreparedExport:
    id: str
    manifest_id: str
    root: str
    guide: dict[str, Any]
    cut_assets: dict[str, dict[str, str]]
    plan_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "manifest_id": self.manifest_id,
            "root": self.root,
            "guide": self.guide,
            "cut_assets": self.cut_assets,
            "plan_path": self.plan_path,
        }


def prepare_export(
    *,
    store: AutomationStore,
    manifest_id: str,
    guide: Mapping[str, Any],
    staging_root: Path,
    capcut_root: Optional[str] = None,
) -> PreparedExport:
    """Validate everything without changing a CapCut project."""

    manifest = store.load_manifest(manifest_id)
    root = find_capcut_draft_root(capcut_root)
    if root is None:
        raise ValueError(
            "CapCut project folder was not found. Start CapCut once or set it manually."
        )
    base, text_template, image_template = _pick_templates(root)
    if base is None or text_template is None or image_template is None:
        raise ValueError(
            "A compatible CapCut project containing text and image/video clips is required."
        )
    selected: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for cut in manifest.cuts:
        candidate = cut.selected_candidate
        requires_selected = cut.asset_type in (
            AssetType.GENERATED_IMAGE,
            AssetType.GENERATED_VIDEO,
        )
        if candidate is None:
            if requires_selected:
                missing.append(cut.id)
            continue
        path = Path(candidate.prepared_path or candidate.original_path or "")
        if not path.is_file():
            missing.append(cut.id)
            continue
        selected[cut.id] = {
            "path": str(path.resolve()),
            "media_kind": candidate.media_kind.value,
            "candidate_id": candidate.id,
        }
    if missing:
        raise ValueError(
            "Selected assets are missing for cuts: " + ", ".join(sorted(missing))
        )
    export_id = uuid4().hex
    directory = Path(staging_root) / manifest_id / "exports" / export_id
    directory.mkdir(parents=True, exist_ok=False)
    plan_path = directory / "prepared-export.json"
    prepared = PreparedExport(
        id=export_id,
        manifest_id=manifest_id,
        root=str(root.resolve()),
        guide=dict(guide),
        cut_assets=selected,
        plan_path=str(plan_path),
    )
    plan_path.write_text(
        json.dumps(prepared.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return prepared


def load_prepared_export(path: Path) -> PreparedExport:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return PreparedExport(
        id=value["id"],
        manifest_id=value["manifest_id"],
        root=value["root"],
        guide=value["guide"],
        cut_assets=value["cut_assets"],
        plan_path=str(Path(path)),
    )


def commit_export(prepared: PreparedExport) -> dict[str, Any]:
    """Perform the existing export only after the immutable plan validates."""

    for value in prepared.cut_assets.values():
        if not Path(value["path"]).is_file():
            raise ValueError(
                "Prepared asset disappeared before commit; prepare the export again."
            )
    result = export_to_capcut_project(
        prepared.guide,
        root_override=prepared.root,
        cut_assets=prepared.cut_assets,
    )
    result["prepared_export_id"] = prepared.id
    result["selected_assets"] = len(prepared.cut_assets)
    return result
