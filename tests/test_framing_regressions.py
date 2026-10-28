"""Offline regressions for original-preserving export and guide storage."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from shortly.core.domain.framing import BODY_VIEWPORT, contain_layout
from shortly.generate.capcut_export import _build_photo_segment, export_via_template
from shortly.apps.personal.automation.capcut_assets import (
    _resolve_material_path, validate_capcut_project,
)
from shortly.generate.research import parse_required_content as research_required
from shortly.generate.script_gen import parse_required_content as script_required
from shortly.service import _reserve_output_dir


def template():
    return {
        "seg": {"id": "seg", "material_id": "photo", "enable_video_mask": True,
                "extra_material_refs": ["mask"], "volume": 1},
        "materials": {
            "videos": [{"id": "photo", "type": "photo", "path": "",
                        "crop": {"upper_left_x": 0.5}}],
            "common_mask": [{"id": "mask", "resource_type": "mirror"}],
        },
    }


class FramingRegressions(unittest.TestCase):
    def test_all_editorial_stages_keep_required_endings_above_generic_preferences(self):
        # Contract against a regression where one later pass silently deletes
        # the explicitly requested event preserved by the previous pass.
        import inspect
        from shortly.generate import script_gen, research, factcheck
        for module in (script_gen, research, factcheck):
            source = inspect.getsource(module)
            self.assertIn("사용자 지정 필수 결말", source)
            self.assertNotIn("사용자가 마케팅 전략을 요청하지 않았다면", source)
            self.assertNotIn("사용자가 마케팅 전략 자체를 요청하지 않았다면", source)

    def test_full_template_export_preserves_image_and_ignores_recent_mask(self):
        self._assert_full_template_export("image")

    def test_full_template_export_preserves_video_and_ignores_recent_mask(self):
        self._assert_full_template_export("video")

    def _assert_full_template_export(self, media_kind):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_dir = root / "fixture-template"
            template_dir.mkdir()
            image = root / ("original.png" if media_kind == "image" else "original.mp4")
            if media_kind == "image":
                Image.new("RGB", (200, 300), "red").save(image)
            else:
                payload = b"\x00" * 76 + (720 << 16).to_bytes(4, "big") + (960 << 16).to_bytes(4, "big")
                image.write_bytes((8 + len(payload)).to_bytes(4, "big") + b"tkhd" + payload)
            original = image.read_bytes()
            image_template = template()
            doc = {
                "id": "original-project", "duration": 1_000_000,
                "canvas_config": {"ratio": "9:16", "width": 1080, "height": 1920},
                "materials": {
                    **image_template["materials"],
                    "texts": [{"id": "text", "type": "text",
                               "content": json.dumps({"text": "제목", "styles": []})}],
                },
                "tracks": [
                    {"type": "text", "segments": [{"id": "text-seg", "material_id": "text",
                     "target_timerange": {"start": 0, "duration": 1_000_000},
                     "clip": {"transform": {"y": 0.71}}}]},
                    {"type": "video", "segments": [image_template["seg"]]},
                ],
            }
            template_json = template_dir / "draft_content.json"
            template_json.write_text(json.dumps(doc), encoding="utf-8")
            template_bytes = template_json.read_bytes()
            guide = {"title": "regression export", "segments": [
                {"index": 0, "narration": "테스트 대사였음.", "duration_sec": 2,
                 "cuts_suggested": 1, "image_suggestions": ["제품"]}]}
            with patch("shortly.generate.capcut_export._pick_generated_video_mask_template",
                       side_effect=AssertionError("must not scan for masks")), patch(
                "shortly.generate.capcut_export._crop_generated_image_safe_top",
                side_effect=AssertionError("must not crop source copies")), patch(
                "shortly.generate.capcut_export._pick_reference_image_template", return_value=image_template), patch(
                "shortly.generate.capcut_export._video_duration_us", return_value=4_000_000):
                result = export_via_template(guide, root_override=str(root),
                    template_name="fixture-template", tts_cfg=None,
                    cut_assets={"s000-c00": {"path": str(image), "media_kind": media_kind,
                                              "asset_type": "generated_" + media_kind}})
            self.assertTrue(result["ok"], result)
            project = Path(result["path"])
            asset_name = "shortly_cuts/cut_01" + image.suffix
            self.assertEqual((project / asset_name).read_bytes(), original)
            self.assertEqual(template_json.read_bytes(), template_bytes)
            self.assertTrue(validate_capcut_project(project, expected_asset_count=1)["passed"])
            exported = json.loads((project / "draft_content.json").read_text(encoding="utf-8"))
            asset = next(m for m in exported["materials"]["videos"]
                         if str(m.get("path", "")).endswith(asset_name))
            self.assertEqual((asset["width"], asset["height"]),
                             (200, 300) if media_kind == "image" else (720, 960))
            segment = next(s for t in exported["tracks"] for s in t["segments"]
                           if s.get("material_id") == asset["id"])
            self.assertEqual(segment["extra_material_refs"], [])
            self.assertEqual(segment["clip"]["transform"]["y"], -0.18)

    def test_all_ratios_remain_inside_shared_body_viewport(self):
        for width, height in [(1024, 1536), (720, 960), (1080, 1920),
                              (1920, 1080), (1000, 1000)]:
            with self.subTest(size=(width, height)):
                scale, y = contain_layout(width, height)
                base = min(1080 / width, 1920 / height)
                visible_w = width * base * scale / 1080
                visible_h = height * base * scale / 1920
                self.assertLessEqual(visible_w, BODY_VIEWPORT["width"] + 1e-7)
                self.assertLessEqual(visible_h, BODY_VIEWPORT["height"] + 1e-7)
                self.assertAlmostEqual((1 - y) / 2, 0.59)

    def test_video_keeps_full_frame_mutes_audio_and_drops_template_mask(self):
        for size in [(720, 960), (1080, 1920), (1920, 1080)]:
            with self.subTest(size=size), patch(
                "shortly.generate.capcut_export._asset_dimensions", return_value=size
            ), patch("shortly.generate.capcut_export._video_duration_us", return_value=4_000_000):
                seg, mats = _build_photo_segment(
                    template(), Path("fixture.mp4"), 0, 3_000_000,
                    *contain_layout(*size), 1, media_kind="video")
                self.assertEqual(seg["volume"], 0)
                self.assertFalse(seg["enable_video_mask"])
                self.assertEqual(seg["extra_material_refs"], [])
                self.assertNotIn("common_mask", mats)
                material = mats["videos"][0]
                self.assertEqual(material["duration"], 4_000_000)
                self.assertEqual(material["crop"]["upper_left_x"], 0)
                self.assertEqual(material["crop"]["lower_right_x"], 1)
                self.assertEqual(material["crop"]["lower_right_y"], 1)
                self.assertEqual(seg["source_timerange"]["duration"], 3_000_000)

    def test_short_video_reports_actionable_error_without_looping(self):
        with patch("shortly.generate.capcut_export._asset_dimensions", return_value=(720, 1280)), patch(
            "shortly.generate.capcut_export._video_duration_us", return_value=4_000_000
        ), self.assertRaisesRegex(ValueError, "4.00초.*5.00초"):
            _build_photo_segment(template(), Path("cut_01.mp4"), 0, 5_000_000,
                                 0.58, -0.18, 1, media_kind="video")

    def test_image_builder_preserves_bytes_dimensions_and_full_crop(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cut_01.png"
            Image.new("RGB", (200, 300), "red").save(path)
            original = path.read_bytes()
            seg, mats = _build_photo_segment(template(), path, 0, 1_000_000,
                                             *contain_layout(200, 300), 1)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(mats["videos"][0]["height"], 300)
            self.assertEqual(mats["videos"][0]["crop"]["upper_left_y"], 0)
            self.assertFalse(seg["enable_video_mask"])

    def test_capcut_placeholder_resolves_existing_local_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "shortly_cuts").mkdir()
            asset = project / "shortly_cuts/cut_01.png"
            asset.write_bytes(b"fixture")
            raw = "##_draftpath_placeholder_example_##/shortly_cuts/cut_01.png"
            self.assertEqual(_resolve_material_path(project, raw), asset.resolve())
            doc = {"duration": 1, "canvas_config": {"ratio": "9:16"},
                   "materials": {"videos": [{"id": "a", "path": raw, "type": "photo"}]},
                   "tracks": [{"segments": [{"material_id": "a"}]}]}
            (project / "draft_content.json").write_text(json.dumps(doc), encoding="utf-8")
            self.assertTrue(validate_capcut_project(project)["passed"])
            with self.assertRaises(ValueError):
                _resolve_material_path(project, "##_draftpath_placeholder_example_##/../escape")

    def test_concurrent_same_title_outputs_never_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _reserve_output_dir(root, "fixed-title")
            (first / "guide.json").write_text("original", encoding="utf-8")
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(lambda _: _reserve_output_dir(root, "fixed-title"), range(12)))
            self.assertEqual(len(set(paths)), 12)
            self.assertNotIn(first, paths)
            self.assertEqual((first / "guide.json").read_text(encoding="utf-8"), "original")

    def test_invalid_output_root_fails_instead_of_retrying_forever(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "outputs"
            root.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                _reserve_output_dir(root, "title")

    def test_validator_checks_actual_video_length_and_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "shortly_cuts").mkdir()
            asset = project / "shortly_cuts/cut_01.mp4"
            asset.write_bytes(b"fixture")
            doc = {"duration": 5_000_000, "canvas_config": {"ratio": "9:16"},
                   "materials": {"videos": [{"id": "a", "path": str(asset),
                       "type": "video", "width": 720, "height": 1280}]},
                   "tracks": [{"segments": [{"material_id": "a", "volume": 0,
                       "clip": {"scale": {"x": 1, "y": 1}, "transform": {"x": 0, "y": 0}},
                       "source_timerange": {"start": 1_000_000, "duration": 4_000_000}}]}]}
            (project / "draft_content.json").write_text(json.dumps(doc), encoding="utf-8")
            with patch("shortly.apps.personal.automation.capcut_assets._video_duration_us", return_value=4_000_000):
                result = validate_capcut_project(project)
            self.assertFalse(result["passed"])
            self.assertTrue(any("원본 길이를 초과" in error for error in result["errors"]))
            self.assertTrue(any("본문 영역" in warning for warning in result["warnings"]))

    def test_research_and_script_preserve_all_required_items(self):
        value = "\n".join(f"{i}. " + str(i) * 250 for i in range(1, 10))
        self.assertEqual(research_required(value), script_required(value))
        self.assertEqual(len(research_required(value)), 9)
        self.assertTrue(all(len(item) == 250 for item in research_required(value)))


if __name__ == "__main__":
    unittest.main()
