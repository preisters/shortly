from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shortly.apps.personal.automation.capcut_assets import (
    prepare_export,
    validate_capcut_project,
)
from shortly.apps.personal.automation.review import (
    qwen_read_only_review,
    technical_review,
)
from shortly.apps.personal.automation.store import AutomationStore
from shortly.core.domain.assets import (
    AssetCandidate,
    AssetType,
    CandidateStatus,
    MediaKind,
)
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest
from shortly.generate.capcut_export import _build_photo_segment
from shortly.llm.base import (
    LLMClient,
    LLMError,
    LLMResponseParseError,
)


class _ReviewLlm(LLMClient):
    available = True
    provider = "local"

    def __init__(self):
        super().__init__("qwen-fixture", "qwen-fixture")

    def complete_json(self, *args, **kwargs):
        return {"passed": True, "summary": "ok", "findings": []}


class _FailedReviewLlm(_ReviewLlm):
    def complete_json(self, *args, **kwargs):
        raise LLMError("connection refused")


class _MalformedReviewLlm(_ReviewLlm):
    def complete_json(self, *args, **kwargs):
        raise LLMResponseParseError("invalid json")


class PersonalReviewAndExportTests(unittest.TestCase):
    def test_qwen_review_returns_report_without_mutating_manifest(self) -> None:
        manifest = build_cut_manifest(
            {"title": "fixture", "segments": [{"duration_sec": 1}]}
        )
        before = manifest.to_dict()
        report = qwen_read_only_review(
            _ReviewLlm(), manifest, technical=technical_review(manifest)
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.reviewer, "local-qwen:qwen-fixture")
        self.assertEqual(manifest.to_dict(), before)

    def test_qwen_review_distinguishes_request_and_parse_failures(self) -> None:
        manifest = build_cut_manifest(
            {"title": "fixture", "segments": [{"duration_sec": 1}]}
        )

        request_failure = qwen_read_only_review(
            _FailedReviewLlm(),
            manifest,
        )
        parse_failure = qwen_read_only_review(
            _MalformedReviewLlm(),
            manifest,
        )

        self.assertEqual(
            request_failure.findings[0].code,
            "local_model_request_failed",
        )
        self.assertIn("LM Studio", request_failure.findings[0].message)
        self.assertEqual(
            parse_failure.findings[0].code,
            "review_parse_failed",
        )

    def test_prepare_export_validates_selected_assets_without_capcut_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = AutomationStore(root / "automation.sqlite3")
            manifest = build_cut_manifest(
                {
                    "title": "fixture",
                    "segments": [
                        {
                            "index": 0,
                            "duration_sec": 1,
                            "image_suggestions": ["생성 일러스트"],
                        }
                    ],
                }
            )
            asset = root / "asset.jpg"
            asset.write_bytes(b"fixture")
            cut = manifest.cuts[0]
            cut.asset_type = AssetType.GENERATED_IMAGE
            candidate = AssetCandidate(
                provider="fake",
                model="fake",
                media_kind=MediaKind.IMAGE,
                status=CandidateStatus.SELECTED,
                prepared_path=str(asset),
            )
            cut.candidates.append(candidate)
            cut.selected_candidate_id = candidate.id
            store.save_manifest(manifest)
            capcut = root / "capcut"
            capcut.mkdir()

            with patch(
                "shortly.apps.personal.automation.capcut_assets.find_capcut_draft_root",
                return_value=capcut,
            ), patch(
                "shortly.apps.personal.automation.capcut_assets._pick_templates",
                return_value=({}, {}, {}),
            ):
                prepared = prepare_export(
                    store=store,
                    manifest_id=manifest.manifest_id,
                    guide={"title": "fixture", "segments": []},
                    staging_root=root / "staging",
                )

            self.assertTrue(Path(prepared.plan_path).is_file())
            self.assertEqual(
                prepared.cut_assets["s000-c00"]["candidate_id"], candidate.id
            )
            self.assertEqual(list(capcut.iterdir()), [])

    def test_capcut_segment_can_reference_generated_video(self) -> None:
        template = {
            "seg": {
                "id": "segment",
                "material_id": "material",
                "extra_material_refs": [],
            },
            "materials": {
                "videos": [{"id": "material", "type": "photo", "path": ""}]
            },
        }
        segment, cloned = _build_photo_segment(
            template,
            Path("fixture.mp4"),
            0,
            1_000_000,
            1.0,
            0.0,
            1,
            media_kind="video",
        )
        material = cloned["videos"][0]
        self.assertEqual(material["type"], "video")
        self.assertEqual(material["duration"], 1_000_000)
        self.assertEqual(segment["source_timerange"]["duration"], 1_000_000)

    def test_committed_capcut_project_validation_resolves_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            cuts = project / "shortly_cuts"
            cuts.mkdir()
            asset = cuts / "cut_01.jpg"
            asset.write_bytes(b"fixture")
            document = {
                "duration": 1_000_000,
                "canvas_config": {"ratio": "9:16"},
                "materials": {
                    "videos": [
                        {
                            "id": "material",
                            "type": "photo",
                            "path": str(asset),
                            "width": 1080,
                            "height": 1920,
                        }
                    ]
                },
                "tracks": [
                    {
                        "type": "video",
                        "segments": [{"material_id": "material"}],
                    }
                ],
            }
            (project / "draft_content.json").write_text(
                __import__("json").dumps(document),
                encoding="utf-8",
            )

            result = validate_capcut_project(
                project, expected_asset_count=1
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["inserted_assets"][0]["type"], "photo")

    def test_committed_capcut_project_validation_reports_missing_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "draft_content.json").write_text(
                __import__("json").dumps(
                    {
                        "duration": 1,
                        "canvas_config": {"ratio": "9:16"},
                        "materials": {},
                        "tracks": [
                            {
                                "segments": [
                                    {"material_id": "missing-material"}
                                ]
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_capcut_project(project)

        self.assertFalse(result["passed"])
        self.assertIn("material references", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
