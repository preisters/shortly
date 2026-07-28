from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shortly.apps.personal.automation.capcut_assets import (
    PreparedExport,
    commit_export,
    load_prepared_export,
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
from shortly.generate.capcut_export import (
    _build_photo_segment,
    _caption_pages,
    _plan_sentences,
    _reference_asset_layout,
    _split_sentences,
)
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
                "shortly.apps.personal.automation.capcut_assets._find_template_project",
                return_value=capcut / "template",
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
            self.assertEqual(
                prepared.cut_assets["s000-c00"]["path"], str(asset.resolve())
            )
            self.assertEqual(list(capcut.iterdir()), [])

    def test_commit_export_reuses_template_tts_and_selected_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "asset.png"
            asset.write_bytes(b"fixture")
            prepared = PreparedExport(
                id="export",
                manifest_id="manifest",
                root=str(root),
                guide={"title": "fixture", "segments": []},
                cut_assets={
                    "s000-c00": {
                        "path": str(asset),
                        "media_kind": "image",
                        "candidate_id": "candidate",
                    }
                },
                plan_path=str(root / "prepared-export.json"),
                template_name="챙맛 템플릿",
            )
            expected = {"ok": False, "reason": "fixture"}

            with patch(
                "shortly.apps.personal.automation.capcut_assets.export_via_template",
                return_value=expected,
            ) as export:
                result = commit_export(
                    prepared,
                    tts_cfg="tts-config",
                    tts_out_dir="tts-output",
                )

        self.assertEqual(result["prepared_export_id"], "export")
        export.assert_called_once_with(
            prepared.guide,
            root_override=str(root),
            template_name="챙맛 템플릿",
            tts_cfg="tts-config",
            tts_out_dir="tts-output",
            cut_assets=prepared.cut_assets,
        )

    def test_legacy_prepared_image_is_upgraded_to_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared_image = root / "prepared-1080x1920.jpg"
            original_image = root / "image-original.png"
            prepared_image.write_bytes(b"prepared")
            original_image.write_bytes(b"original")
            plan = root / "prepared-export.json"
            plan.write_text(
                __import__("json").dumps(
                    {
                        "id": "export",
                        "manifest_id": "manifest",
                        "root": str(root),
                        "guide": {},
                        "cut_assets": {
                            "s000-c00": {
                                "path": str(prepared_image),
                                "media_kind": "image",
                                "candidate_id": "candidate",
                            }
                        },
                        "plan_path": str(plan),
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_prepared_export(plan)

        self.assertEqual(
            loaded.cut_assets["s000-c00"]["path"],
            str(original_image.resolve()),
        )

    def test_reference_layout_follows_manual_aspect_ratio_medians(self) -> None:
        self.assertEqual(_reference_asset_layout(1024, 1536), (0.64, -0.21))
        self.assertEqual(
            _reference_asset_layout(1080, 1920, "video"),
            (0.74, -0.1),
        )
        self.assertEqual(_reference_asset_layout(1000, 1000), (0.72, -0.12))
        self.assertEqual(_reference_asset_layout(1280, 720), (0.8, -0.1))

    def test_caption_planner_preserves_quoted_sentence(self) -> None:
        text = "사실 처음 먹어보면 '어? 이거 어디서 먹어본 맛인데?' 싶다고"

        self.assertEqual(_split_sentences(text), [text])
        pages = _caption_pages(text)

        self.assertTrue(
            any(
                "'어? 이거 어디서 먹어본 맛인데?'" in line
                for page in pages
                for line in page
            )
        )

    def test_two_line_caption_accumulates_with_tts_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = _plan_sentences(
                [
                    {
                        "narration": (
                            "요즘 난리난 상하이 버터떡, "
                            "언론이 억지로 밀어버린 거라고 함"
                        )
                    }
                ],
                None,
                Path(temporary),
            )

        first, second = plan["captions"][:2]
        self.assertTrue(first["text"].endswith("\n"))
        self.assertTrue(second["text"].startswith("\n"))
        self.assertEqual(first["layer"], 0)
        self.assertEqual(second["layer"], 1)
        self.assertGreater(second["start"], first["start"])
        self.assertEqual(
            first["start"] + first["dur"],
            second["start"] + second["dur"],
        )

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
                "canvas_config": {
                    "ratio": "original",
                    "width": 1080,
                    "height": 1920,
                },
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
