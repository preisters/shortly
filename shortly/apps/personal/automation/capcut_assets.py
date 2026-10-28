"""Two-phase validation and commit for selected assets in CapCut exports."""

from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from ....core.domain.assets import AssetType
from ....core.domain.framing import BODY_VIEWPORT
from ....generate.capcut_export import (
    _find_template_project,
    _pick_templates,
    export_via_template,
    find_capcut_draft_root,
    _video_duration_us,
    prepare_body_plan, plan_cut_durations, export_config_signature, _safe_folder,
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
    version: int = 2
    manifest_signature: str = ""
    config_signature: str = ""
    body_plan: dict[str, Any] = field(default_factory=dict)
    file_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "manifest_id": self.manifest_id,
            "root": self.root,
            "guide": self.guide,
            "cut_assets": self.cut_assets,
            "plan_path": self.plan_path,
            "template_name": self.template_name,
            "version": self.version,
            "manifest_signature": self.manifest_signature,
            "config_signature": self.config_signature,
            "body_plan": self.body_plan,
            "file_hashes": self.file_hashes,
        }


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _manifest_signature(manifest) -> str:
    return _digest({"source": manifest.source_guide_hash, "cuts": [
        {"id": c.id, "type": c.asset_type.value, "disclosure": c.disclosure_text,
         "selected": c.selected_candidate.to_dict() if c.selected_candidate else None}
        for c in manifest.cuts]})


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_export(
    *,
    store: AutomationStore,
    manifest_id: str,
    guide: Mapping[str, Any],
    staging_root: Path,
    capcut_root: Optional[str] = None,
    template_name: str = "챙맛 템플릿",
    tts_cfg=None,
    tts_out_dir: Optional[str] = None,
) -> PreparedExport:
    """Validate everything without changing a CapCut project."""

    manifest = store.load_manifest(manifest_id)
    if manifest.source_guide_hash != _digest(dict(guide)):
        raise ValueError("대본과 에셋 명세가 일치하지 않습니다. 해당 가이드에서 에셋 검토를 다시 열어 주세요.")
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
            if original.is_file()
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
            "asset_type": cut.asset_type.value,
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
    cache = (Path(tts_out_dir or str(Path(staging_root) / "tts")) / _safe_folder(manifest.title)).resolve()
    body_plan = prepare_body_plan(dict(guide), tts_cfg, cache)
    durations = plan_cut_durations(dict(guide), body_plan)
    for cut_id, asset in selected.items():
        if cut_id not in durations:
            raise ValueError(f"대사 타임라인에 없는 에셋입니다: {cut_id}")
        if asset["media_kind"] == "video":
            actual = _video_duration_us(Path(asset["path"]))
            if actual is None:
                raise ValueError(f"{cut_id}: 영상 길이를 읽지 못했습니다. ffprobe 설치와 영상 파일을 확인하세요.")
            if durations[cut_id] > actual + 50_000:
                raise ValueError(f"{cut_id}: 영상은 {actual / 1_000_000:.2f}초, 확정 컷은 "
                                 f"{durations[cut_id] / 1_000_000:.2f}초입니다. 컷 구성을 조정하거나 더 긴 영상을 선택하세요.")
    paths = [a["path"] for a in selected.values()]
    paths += [a["wav"] for a in body_plan["audios"] if a.get("wav")]
    file_hashes = {p: _file_digest(Path(p)) for p in paths}
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
        manifest_signature=_manifest_signature(manifest),
        config_signature=export_config_signature(tts_cfg),
        body_plan=body_plan,
        file_hashes=file_hashes,
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
            int(value.get("version") or 1) < 2
            and upgraded.get("media_kind") == "image"
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
        version=int(value.get("version") or 1),
        manifest_signature=str(value.get("manifest_signature") or ""),
        config_signature=str(value.get("config_signature") or ""),
        body_plan=dict(value.get("body_plan") or {}),
        file_hashes=dict(value.get("file_hashes") or {}),
    )


def _resolve_material_path(project: Path, raw_path: str) -> Path:
    normalized = raw_path.replace("\\", "/")
    match = re.match(r"^##_draftpath_placeholder_[^/]+_##/(.*)$", normalized)
    if match:
        resolved = (project / match.group(1)).resolve()
        if not resolved.is_relative_to(project.resolve()):
            raise ValueError("CapCut project-relative asset escapes its project")
        return resolved
    path = Path(normalized)
    return path if path.is_absolute() else project / path


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
    segments_by_material: dict[str, list[dict[str, Any]]] = {}
    for track in document.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        for segment in track.get("segments") or []:
            if isinstance(segment, dict) and segment.get("material_id"):
                material_id = str(segment["material_id"])
                referenced_ids.append(material_id)
                segments_by_material.setdefault(material_id, []).append(segment)
    missing_refs = sorted(
        {material_id for material_id in referenced_ids if material_id not in material_index}
    )
    if missing_refs:
        errors.append(f"{len(missing_refs)} material references do not resolve")

    common_mask_ids = {
        str(item.get("id"))
        for item in (materials.get("common_mask") or [])
        if isinstance(item, dict) and item.get("id")
    }

    inserted_assets: list[dict[str, Any]] = []
    for material in material_index.values():
        raw_path = str(material.get("path") or "")
        if not raw_path:
            continue
        try:
            path = _resolve_material_path(project, raw_path)
        except ValueError:
            errors.append("invalid project-relative asset path")
            continue
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
        material_segments = segments_by_material.get(
            str(material.get("id")), []
        )
        segment_volumes = [
            float(segment.get("volume", 1.0))
            for segment in material_segments
        ]
        if media_type == "video" and any(
            abs(volume) > 0.0001 for volume in segment_volumes
        ):
            errors.append(
                f"inserted video audio is not muted: {path.name}"
            )
        caption_masked = bool(material_segments) and all(
            any(
                str(ref) in common_mask_ids
                for ref in (segment.get("extra_material_refs") or [])
            )
            for segment in material_segments
        )
        # No mask is needed for contain placement. A mask on an old/manual
        # project is informational, not proof of correct caption clearance.
        if caption_masked and material_segments:
            warnings.append(f"에셋에 마스크가 있습니다. 실제 잘림을 확인하세요: {path.name}")
        crop = material.get("crop") or {}
        full_crop = {"upper_left_x": 0, "upper_left_y": 0,
                     "upper_right_x": 1, "upper_right_y": 0,
                     "lower_left_x": 0, "lower_left_y": 1,
                     "lower_right_x": 1, "lower_right_y": 1}
        if crop and any(abs(float(crop.get(key, value)) - value) > 0.0001
                        for key, value in full_crop.items()):
            warnings.append(f"원본 일부를 자르는 크롭이 적용되어 있습니다: {path.name}")
        width, height = int(material.get("width") or 0), int(material.get("height") or 0)
        if width > 0 and height > 0 and not caption_masked:
            # Non-destructive canvas-fit geometry. Warn on old/manual layouts;
            # do not treat a deliberate manual crop as missing media.
            base = min(1080 / width, 1920 / height)
            for segment in material_segments:
                clip = segment.get("clip") or {}
                if not clip:
                    continue
                scale = clip.get("scale") or {}
                transform = clip.get("transform") or {}
                half_w = width * base * abs(float(scale.get("x", 1))) / 2160
                half_h = height * base * abs(float(scale.get("y", 1))) / 3840
                cx = (1 + float(transform.get("x", 0))) / 2
                cy = (1 - float(transform.get("y", 0))) / 2
                viewport = BODY_VIEWPORT
                if (cx - half_w < viewport["left"] - 0.001
                    or cx + half_w > viewport["left"] + viewport["width"] + 0.001
                    or cy - half_h < viewport["top"] - 0.001
                    or cy + half_h > viewport["top"] + viewport["height"] + 0.001):
                    warnings.append(f"에셋이 기본 본문 영역을 벗어납니다. 자막 겹침을 확인하세요: {path.name}")
                    break
        if media_type == "video" and exists:
            actual = _video_duration_us(path)
            if actual is None:
                warnings.append(f"영상 길이를 확인하지 못했습니다: {path.name}")
            else:
                for segment in material_segments:
                    source = segment.get("source_timerange") or {}
                    end = int(source.get("start") or 0) + int(source.get("duration") or 0)
                    if end > actual + 50_000:
                        errors.append(f"영상 사용 구간이 원본 길이를 초과합니다: {path.name}")
        inserted_assets.append(
            {
                "name": path.name,
                "type": media_type,
                "exists": exists,
                "width": material.get("width"),
                "height": material.get("height"),
                "duration": material.get("duration"),
                "segment_volumes": segment_volumes,
                "caption_masked": caption_masked,
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
    store: Optional[AutomationStore] = None,
) -> dict[str, Any]:
    """Perform the existing export only after the immutable plan validates."""

    if prepared.version != 2 or not prepared.body_plan or store is None:
        raise ValueError("이전 내보내기 준비 결과는 사용할 수 없습니다. 준비를 다시 실행하세요.")
    manifest = store.load_manifest(prepared.manifest_id)
    if manifest.source_guide_hash != _digest(prepared.guide) or _manifest_signature(manifest) != prepared.manifest_signature:
        raise ValueError("준비 후 대본 또는 에셋 선택이 변경됐습니다. 내보내기 준비를 다시 실행하세요.")
    if prepared.config_signature != export_config_signature(tts_cfg):
        raise ValueError("준비 후 TTS 설정이 변경됐습니다. 내보내기 준비를 다시 실행하세요.")
    for filename, expected in prepared.file_hashes.items():
        path = Path(filename)
        if not path.is_file() or _file_digest(path) != expected:
            raise ValueError("준비 후 에셋 또는 음성 파일이 변경됐습니다. 내보내기 준비를 다시 실행하세요.")
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
        body_plan=prepared.body_plan,
        validator=lambda path: validate_capcut_project(path, expected_asset_count=len(prepared.cut_assets)),
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
