"""Two-phase validation and commit for selected assets in CapCut exports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from ....core.domain.assets import AssetType
from ....generate.capcut_export import (
    _find_template_project,
    _pick_templates,
    export_via_template,
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
    template_name: str = "챙맛 템플릿"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "manifest_id": self.manifest_id,
            "root": self.root,
            "guide": self.guide,
            "cut_assets": self.cut_assets,
            "plan_path": self.plan_path,
            "template_name": self.template_name,
        }


def prepare_export(
    *,
    store: AutomationStore,
    manifest_id: str,
    guide: Mapping[str, Any],
    staging_root: Path,
    capcut_root: Optional[str] = None,
    template_name: str = "챙맛 템플릿",
) -> PreparedExport:
    """Validate everything without changing a CapCut project."""

    manifest = store.load_manifest(manifest_id)
    root = find_capcut_draft_root(capcut_root)
    if root is None:
        raise ValueError(
            "CapCut project folder was not found. Start CapCut once or set it manually."
        )
    template = _find_template_project(root, template_name)
    base, text_template, image_template = _pick_templates(root)
    if template is None:
        raise ValueError(
            f"CapCut template project was not found: {template_name}"
        )
    if base is None or text_template is None or image_template is None:
        raise ValueError(
            "A compatible CapCut project containing text and image/video clips is required."
        )
    selected: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for cut in manifest.cuts:
        candidate = cut.selected_candidate
        requires_selected = cut.asset_type in (
            AssetType.STOCK_IMAGE,
            AssetType.GENERATED_IMAGE,
            AssetType.GENERATED_VIDEO,
        )
        if candidate is None:
            if requires_selected:
                missing.append(cut.id)
            continue
        original = Path(candidate.original_path or "")
        prepared_path = Path(candidate.prepared_path or "")
        path = (
            original
            if candidate.media_kind.value == "image" and original.is_file()
            else prepared_path
            if prepared_path.is_file()
            else original
        )
        if not path.is_file():
            missing.append(cut.id)
            continue
        selected[cut.id] = {
            "path": str(path.resolve()),
            "media_kind": candidate.media_kind.value,
            "candidate_id": candidate.id,
            "disclosure_text": (
                cut.disclosure_text
                if cut.asset_type
                in (
                    AssetType.GENERATED_IMAGE,
                    AssetType.GENERATED_VIDEO,
                )
                else ""
            ),
        }
    if missing:
        raise ValueError(
            "내보내기에 필요한 에셋이 선택되지 않았거나 파일이 없습니다: "
            + ", ".join(sorted(missing))
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
        template_name=template_name,
    )
    plan_path.write_text(
        json.dumps(prepared.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return prepared


def load_prepared_export(path: Path) -> PreparedExport:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    cut_assets = dict(value["cut_assets"])
    for cut_id, item in cut_assets.items():
        upgraded = dict(item)
        asset_path = Path(str(upgraded.get("path") or ""))
        if (
            upgraded.get("media_kind") == "image"
            and asset_path.name.startswith("prepared-")
        ):
            originals = sorted(asset_path.parent.glob("*-original.*"))
            if originals:
                upgraded["path"] = str(originals[0].resolve())
        cut_assets[cut_id] = upgraded
    return PreparedExport(
        id=value["id"],
        manifest_id=value["manifest_id"],
        root=value["root"],
        guide=value["guide"],
        cut_assets=cut_assets,
        plan_path=str(Path(path)),
        template_name=value.get("template_name") or "챙맛 템플릿",
    )


def validate_capcut_project(
    project_dir: Path,
    *,
    expected_asset_count: Optional[int] = None,
) -> dict[str, Any]:
    """Validate a committed project without opening or modifying CapCut."""

    project = Path(project_dir)
    content_path = project / "draft_content.json"
    errors: list[str] = []
    warnings: list[str] = []
    try:
        document = json.loads(content_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return {
            "passed": False,
            "errors": [f"draft_content.json could not be read: {type(exc).__name__}"],
            "warnings": [],
            "inserted_assets": [],
        }

    canvas = document.get("canvas_config") or {}
    canvas_ratio = str(canvas.get("ratio") or "")
    canvas_width = int(canvas.get("width") or 0)
    canvas_height = int(canvas.get("height") or 0)
    if canvas_ratio != "9:16" and not (
        canvas_ratio == "original"
        and canvas_width == 1080
        and canvas_height == 1920
    ):
        errors.append("canvas is not a 1080x1920 vertical project")
    if int(document.get("duration") or 0) <= 0:
        errors.append("project duration is missing or invalid")

    materials = document.get("materials") or {}
    material_index = {
        item.get("id"): item
        for values in materials.values()
        if isinstance(values, list)
        for item in values
        if isinstance(item, dict) and item.get("id")
    }
    referenced_ids: list[str] = []
    for track in document.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        for segment in track.get("segments") or []:
            if isinstance(segment, dict) and segment.get("material_id"):
                referenced_ids.append(str(segment["material_id"]))
    missing_refs = sorted(
        {material_id for material_id in referenced_ids if material_id not in material_index}
    )
    if missing_refs:
        errors.append(f"{len(missing_refs)} material references do not resolve")

    inserted_assets: list[dict[str, Any]] = []
    for material in material_index.values():
        raw_path = str(material.get("path") or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.parent.name != "shortly_cuts" or not path.name.startswith("cut_"):
            continue
        exists = path.is_file()
        if not exists:
            errors.append(f"inserted asset is missing: {path.name}")
        media_type = str(material.get("type") or "")
        if media_type not in ("photo", "video"):
            warnings.append(
                f"inserted asset has unexpected material type: {media_type or 'empty'}"
            )
        inserted_assets.append(
            {
                "name": path.name,
                "type": media_type,
                "exists": exists,
                "width": material.get("width"),
                "height": material.get("height"),
                "duration": material.get("duration"),
            }
        )
    if (
        expected_asset_count is not None
        and len(inserted_assets) != expected_asset_count
    ):
        errors.append(
            "inserted asset count mismatch: "
            f"expected {expected_asset_count}, got {len(inserted_assets)}"
        )
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "inserted_assets": inserted_assets,
        "material_references": len(referenced_ids),
        "track_count": len(document.get("tracks") or []),
    }


def commit_export(
    prepared: PreparedExport,
    *,
    tts_cfg=None,
    tts_out_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Perform the existing export only after the immutable plan validates."""

    for value in prepared.cut_assets.values():
        if not Path(value["path"]).is_file():
            raise ValueError(
                "Prepared asset disappeared before commit; prepare the export again."
            )
    result = export_via_template(
        prepared.guide,
        root_override=prepared.root,
        template_name=prepared.template_name,
        tts_cfg=tts_cfg,
        tts_out_dir=tts_out_dir,
        cut_assets=prepared.cut_assets,
    )
    result["prepared_export_id"] = prepared.id
    result["selected_assets"] = len(prepared.cut_assets)
    if result.get("ok") and result.get("path"):
        validation = validate_capcut_project(
            Path(result["path"]),
            expected_asset_count=len(prepared.cut_assets),
        )
        result["validation"] = validation
        if not validation["passed"]:
            result["ok"] = False
            result["reason"] = (
                "CapCut project was created but post-commit validation failed."
            )
    return result
