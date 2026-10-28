"""Offline failure-path coverage for prepared exports and atomic publishing."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from shortly.apps.personal.automation.capcut_assets import prepare_export, commit_export, load_prepared_export
from shortly.apps.personal.automation.store import AutomationStore
from shortly.core.domain.assets import AssetCandidate, AssetType, MediaKind, CandidateStatus
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest
from shortly.generate.capcut_export import export_via_template, _layout_params, export_to_capcut_project
from shortly.generate.research import build_queries
from test_tts_normalization import _write_tone


class ExportTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "capcut"
        self.template = self.root / "template"
        self.template.mkdir(parents=True)
        self.image = self.base / "image.png"
        Image.new("RGB", (200, 300), "red").save(self.image)
        doc = {"id": "template-id", "duration": 1_000_000,
               "canvas_config": {"ratio": "9:16", "width": 1080, "height": 1920},
               "materials": {
                   "videos": [{"id": "photo", "type": "photo", "path": ""}],
                   "audios": [{"id": "audio", "type": "extract_music", "path": ""}],
                   "texts": [{"id": "text", "type": "text", "content": json.dumps({"text": "제목", "styles": []})}]},
               "tracks": [
                   {"type": "text", "segments": [{"id": "t", "material_id": "text", "target_timerange": {"start": 0, "duration": 1_000_000}, "clip": {"transform": {"y": .71}}}]},
                   {"type": "video", "segments": [{"id": "v", "material_id": "photo"}]},
                   {"type": "audio", "segments": [{"id": "a", "material_id": "audio", "target_timerange": {"start": 0, "duration": 1_000_000}}]},
               ]}
        (self.template / "draft_content.json").write_text(json.dumps(doc), encoding="utf-8")
        self.guide = {"title": "transaction", "segments": [{"index": 0, "duration_sec": 1,
                      "narration": "확인용 대사였음.", "cuts_suggested": 1, "image_suggestions": ["제품"]}]}
        self.store = AutomationStore(self.base / "automation.sqlite")
        self.manifest = build_cut_manifest(self.guide)
        cut = self.manifest.cuts[0]
        cut.asset_type = AssetType.GENERATED_IMAGE
        self.candidate = AssetCandidate(media_kind=MediaKind.IMAGE, status=CandidateStatus.SELECTED, original_path=str(self.image))
        cut.candidates = [self.candidate]
        cut.selected_candidate_id = self.candidate.id
        self.store.save_manifest(self.manifest)
        self.kwargs = dict(root_override=str(self.root), template_name="template",
                           cut_assets={cut.id: {"path": str(self.image), "media_kind": "image"}})

    def prepare(self, **extra):
        return prepare_export(store=self.store, manifest_id=self.manifest.manifest_id,
            guide=self.guide, staging_root=self.base / "assets", capcut_root=str(self.root),
            template_name="template", **extra)

    def assert_not_published(self):
        self.assertEqual([p.name for p in self.root.iterdir()], ["template"])
        staging = self.base / ".shortly-staging"
        if staging.exists():
            self.assertEqual(list(staging.iterdir()), [])

    def test_wrong_guide_rejected_before_synthesis(self):
        self.guide["title"] = "different"
        with patch("shortly.tts.synth_to_file") as synth, self.assertRaisesRegex(ValueError, "일치하지"):
            self.prepare()
        synth.assert_not_called()
        self.assert_not_published()

    def test_commit_reuses_frozen_plan_without_recalculating_body(self):
        plan = self.prepare()
        plan = load_prepared_export(Path(plan.plan_path))
        with patch("shortly.generate.capcut_export._plan_sentences", side_effect=AssertionError("no replan")):
            result = commit_export(plan, store=self.store)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["validation"]["passed"])
        document = json.loads((Path(result["path"]) / "draft_content.json").read_text(encoding="utf-8"))
        self.assertNotIn(".shortly-staging", json.dumps(document))
        self.assertTrue((Path(result["path"]) / "shortly_cuts/cut_01.png").is_file())

    def test_changed_selection_rejected(self):
        plan = self.prepare()
        self.manifest.cuts[0].selected_candidate_id = None
        self.store.save_manifest(self.manifest)
        with self.assertRaisesRegex(ValueError, "선택이 변경"):
            commit_export(plan, store=self.store)
        self.assert_not_published()

    def test_changed_file_rejected(self):
        plan = self.prepare()
        self.image.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "파일이 변경"):
            commit_export(plan, store=self.store)
        self.assert_not_published()

    def test_changed_tts_settings_rejected(self):
        plan = self.prepare()
        with self.assertRaisesRegex(ValueError, "TTS 설정"):
            commit_export(plan, store=self.store, tts_cfg=SimpleNamespace(enabled=True))
        self.assert_not_published()

    def test_missing_prepared_voice_is_rejected_without_paid_retry(self):
        cfg = SimpleNamespace(enabled=True)
        with patch("shortly.tts.synth_to_file", side_effect=self.synth):
            plan = self.prepare(tts_cfg=cfg)
        Path(plan.body_plan["audios"][0]["wav"]).unlink()
        with patch("shortly.tts.synth_to_file") as synth, self.assertRaisesRegex(ValueError, "파일이 변경"):
            commit_export(plan, store=self.store, tts_cfg=cfg)
        synth.assert_not_called()
        self.assert_not_published()

    def test_prepare_http_requires_tts_approval_and_commit_passes_store(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shortly.apps.personal.automation.api import create_router
        from shortly.apps.personal.automation.settings import PersonalAutomationSettings
        settings = PersonalAutomationSettings(state_dir=self.base / "state", artifact_dir=self.base)
        base = SimpleNamespace(typecast_config=lambda: SimpleNamespace(enabled=True),
                               capcut_template="template", tts_output_dir=self.base / "tts")
        app = FastAPI()
        app.include_router(create_router(settings, self.store, SimpleNamespace(), service_api=SimpleNamespace()))
        with patch("shortly.apps.personal.automation.api.get_settings", return_value=base), patch(
            "shortly.apps.personal.automation.api.prepare_export") as prepare, patch(
            "shortly.apps.personal.automation.api.load_prepared_export", return_value="plan"), patch(
            "shortly.apps.personal.automation.api.commit_export", return_value={"ok": True}) as commit:
            prepare.return_value.to_dict.return_value = {"id": "fixture"}
            with TestClient(app) as client:
                request = {"manifest_id": self.manifest.manifest_id, "guide": self.guide}
                self.assertEqual(client.post("/api/personal/exports/prepare", json=request).status_code, 400)
                prepare.assert_not_called()
                self.assertEqual(client.post("/api/personal/exports/prepare", json={**request, "approve_tts": True}).status_code, 200)
                self.assertTrue(prepare.call_args.kwargs["tts_cfg"].enabled)
                response = client.post("/api/personal/exports/commit", json={"plan_path": str(settings.assets_dir / "fixture.json")})
                self.assertEqual(response.status_code, 200)
                self.assertIs(commit.call_args.kwargs["store"], self.store)

    def test_title_tts_failure_never_publishes(self):
        with patch("shortly.tts.synth_to_file", side_effect=RuntimeError("fixture unavailable")):
            result = export_via_template(self.guide, **self.kwargs, tts_cfg=SimpleNamespace(enabled=True))
        self.assertFalse(result["ok"])
        self.assertIn("제목 TTS", result["reason"])
        self.assert_not_published()

    @staticmethod
    def synth(cfg, text, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_tone(path, 5000, seconds=1)
        return 1_000_000

    def test_body_tts_failure_never_publishes(self):
        def fail_body(cfg, text, path):
            if text.endswith("?"):
                return self.synth(cfg, text, path)
            raise RuntimeError("body failure fixture")
        with patch("shortly.tts.synth_to_file", side_effect=fail_body):
            result = export_via_template(self.guide, **self.kwargs, tts_cfg=SimpleNamespace(enabled=True))
        self.assertFalse(result["ok"])
        self.assertIn("본문 TTS", result["reason"])
        self.assert_not_published()

    def test_successful_tts_commit_uses_prepared_body_and_adds_title(self):
        cfg = SimpleNamespace(enabled=True)
        with patch("shortly.tts.synth_to_file", side_effect=self.synth):
            plan = self.prepare(tts_cfg=cfg)
        with patch("shortly.tts.synth_to_file", side_effect=self.synth) as synth:
            result = commit_export(plan, store=self.store, tts_cfg=cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual(synth.call_count, 1)  # title only
        self.assertEqual(result["audio_clips"], 2)
        self.assertTrue(result["tts_title"])
        self.assertEqual(result["tts_reused"], 1)

    def test_mid_export_failure_leaves_no_partial_project(self):
        with patch("shortly.generate.capcut_export._build_photo_segment", side_effect=RuntimeError("fixture write failure")):
            result = export_via_template(self.guide, **self.kwargs)
        self.assertFalse(result["ok"])
        self.assert_not_published()

    def test_validation_failure_never_publishes(self):
        result = export_via_template(self.guide, **self.kwargs, validator=lambda _: {"passed": False})
        self.assertFalse(result["ok"])
        self.assert_not_published()

    def test_final_duration_rejects_short_video_during_prepare(self):
        self.guide["segments"][0]["narration"] = "가나다라마바사아자차카타파하" * 4
        self.manifest = build_cut_manifest(self.guide)
        cut = self.manifest.cuts[0]
        cut.asset_type = AssetType.GENERATED_VIDEO
        self.candidate.media_kind = MediaKind.VIDEO
        cut.candidates = [self.candidate];cut.selected_candidate_id = self.candidate.id
        self.store.save_manifest(self.manifest)
        with patch("shortly.apps.personal.automation.capcut_assets._video_duration_us", return_value=4_000_000), self.assertRaisesRegex(ValueError, "7.56초"):
            self.prepare()
        self.assert_not_published()

    def test_all_explicit_research_questions_are_searched(self):
        required = [f"필수항목 {i} 자료 확인" for i in range(8)]
        focus = [f"추가쟁점 {i} 공식 확인" for i in range(5)]
        queries = build_queries("불닭 덴마크 리콜", required_content="\n".join(required), research_focus="\n".join(focus))
        self.assertTrue(set(required + focus).issubset(queries))

    def test_legacy_explicit_image_layout_is_preserved(self):
        layout = {"image": {"h": .5, "cy": .65}}
        result = export_to_capcut_project(self.guide, root_override=str(self.root), layout=layout, cut_assets=self.kwargs["cut_assets"])
        self.assertTrue(result["ok"], result)
        doc = json.loads((Path(result["path"]) / "draft_content.json").read_text(encoding="utf-8"))
        material = next(m for m in doc["materials"]["videos"] if str(m.get("path", "")).endswith("cut_01.png"))
        segment = next(s for t in doc["tracks"] for s in t["segments"] if s.get("material_id") == material["id"])
        self.assertEqual(segment["clip"]["scale"]["x"], _layout_params(layout)["img_scale"])
        self.assertEqual(segment["clip"]["transform"]["y"], _layout_params(layout)["img_y"])
