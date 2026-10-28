import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from shortly.apps.personal.assets.title_video import (
    black_edge_bands, cover_filter, cover_window, generate_once,
    make_preflight, validate_preflight,
)
from shortly.core.ports.assets import VideoGenerationRequest


class TitleFramingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ref = self.root / "reference.png"
        Image.new("RGB", (1600, 900), "tan").save(self.ref)
        self.prompt = "Keep two complete products inside the inspected crop"
        self.stamp = make_preflight(self.ref, self.prompt, model="test",
            aspect_ratio="16:9", target=(1080, 828),
            subject_bounds=(.25, .25, .75, .75), reviewed=True)

    def test_cover_crop_has_no_letterboxing_or_stretch(self):
        crop = cover_window(1600, 900, (1080, 828))
        self.assertAlmostEqual(crop[2] - crop[0], .73369565)
        self.assertEqual(crop[1::2], (0., 1.))
        vf = cover_filter((1080, 828))
        self.assertIn("force_original_aspect_ratio=increase", vf)
        self.assertNotIn("pad", vf)

    def test_wide_products_and_unreviewed_input_fail_before_cost(self):
        for bounds, reviewed in [((.05, .25, .95, .75), True), ((.25, .25, .75, .75), False)]:
            with self.assertRaises(ValueError):
                make_preflight(self.ref, self.prompt, model="test", aspect_ratio="16:9",
                    target=(1080, 828), subject_bounds=bounds, reviewed=reviewed)

    def test_changes_in_reference_prompt_model_or_destination_invalidate_review(self):
        for prompt, model in [("different", "test"), (self.prompt, "changed-model")]:
            with self.assertRaises(ValueError):
                validate_preflight(self.stamp, self.ref, prompt, model=model)
        changed = dict(self.stamp, target=[1920, 1080])
        with self.assertRaises(ValueError):
            validate_preflight(changed, self.ref, self.prompt, model="test")
        Image.new("RGB", (1600, 900), "blue").save(self.ref)
        with self.assertRaises(ValueError):
            validate_preflight(self.stamp, self.ref, self.prompt, model="test")

    def test_reference_must_match_requested_output_aspect(self):
        with self.assertRaises(ValueError):
            make_preflight(self.ref, self.prompt, model="test", aspect_ratio="9:16",
                target=(1080, 828), subject_bounds=(.25, .25, .75, .75), reviewed=True)

    def test_detects_embedded_letterbox_and_pillarbox(self):
        frame = Image.new("RGB", (320, 246), "tan")
        ImageDraw.Draw(frame).rectangle((0, 0, 319, 26), fill="black")
        ImageDraw.Draw(frame).rectangle((0, 220, 319, 245), fill="black")
        self.assertEqual(black_edge_bands(frame)["top"], 27)
        self.assertEqual(black_edge_bands(frame)["bottom"], 26)
        pillar = Image.new("RGB", (320, 246), "white")
        ImageDraw.Draw(pillar).rectangle((0, 0, 35, 245), fill="#080808")
        self.assertEqual(black_edge_bands(pillar)["left"], 36)

    def test_dark_subject_or_thin_edge_is_not_full_frame_bar(self):
        frame = Image.new("RGB", (320, 246), "tan")
        ImageDraw.Draw(frame).rectangle((90, 0, 200, 245), fill="black")
        self.assertFalse(any(black_edge_bands(frame).values()))

    def test_failed_job_is_not_automatically_retried_and_masks_credentials(self):
        class FailedProvider:
            calls = 0
            def submit(self, request):
                self.calls += 1
                raise RuntimeError("credential private-value quota error")
        provider = FailedProvider()
        request = VideoGenerationRequest(prompt=self.prompt, model="test", aspect_ratio="16:9",
            duration_seconds=4, source_image=self.ref.read_bytes(), source_image_mime_type="image/png")
        kwargs = dict(preflight=self.stamp, reference=self.ref, job_path=self.root / "job.json",
            output_path=self.root / "video.mp4", approved=True, estimated_cost_usd=.4,
            secrets=("private-value",))
        with self.assertRaises(RuntimeError):
            generate_once(provider, request, **kwargs)
        with self.assertRaises(FileExistsError):
            generate_once(provider, request, **kwargs)
        self.assertEqual(provider.calls, 1)
        self.assertNotIn("private-value", (self.root / "job.json").read_text("utf8"))

    def test_missing_approval_or_mismatched_request_never_calls_provider(self):
        class NoCalls:
            def submit(self, request):
                raise AssertionError("Must not make a paid call")
        for approved, aspect in [(False, "16:9"), (True, "9:16")]:
            request = VideoGenerationRequest(prompt=self.prompt, model="test", aspect_ratio=aspect,
                duration_seconds=4, source_image=self.ref.read_bytes())
            with self.assertRaises(ValueError):
                generate_once(NoCalls(), request, preflight=self.stamp, reference=self.ref,
                    job_path=self.root / "job.json", output_path=self.root / "video.mp4",
                    approved=approved, estimated_cost_usd=.4)
        self.assertFalse((self.root / "job.json").exists())

    def test_success_remains_pending_qa_and_cannot_be_resubmitted(self):
        class Provider:
            calls = 0
            def submit(self, request):
                self.calls += 1
                return SimpleNamespace(id="fixture", completed=True,
                    asset=SimpleNamespace(data=b"offline-fixture"))
        provider = Provider()
        request = VideoGenerationRequest(prompt=self.prompt, model="test", aspect_ratio="16:9",
            duration_seconds=4, source_image=self.ref.read_bytes(), source_image_mime_type="image/png")
        kwargs = dict(preflight=self.stamp, reference=self.ref, job_path=self.root / "job.json",
            output_path=self.root / "video.mp4", approved=True, estimated_cost_usd=.4)
        report = generate_once(provider, request, **kwargs)
        self.assertEqual(report["status"], "generated_pending_visual_qa")
        self.assertIsNone(report["actual_cost_usd"])
        with self.assertRaises(FileExistsError):
            generate_once(provider, request, **kwargs)
        self.assertEqual(provider.calls, 1)

    def test_different_duration_or_reference_bytes_never_spend(self):
        class NoCalls:
            def submit(self, request):
                raise AssertionError("Must not make a paid call")
        for seconds, data in [(10, self.ref.read_bytes()), (4, b"other reference")]:
            request = VideoGenerationRequest(prompt=self.prompt, model="test", aspect_ratio="16:9",
                duration_seconds=seconds, source_image=data)
            with self.assertRaises(ValueError):
                generate_once(NoCalls(), request, preflight=self.stamp, reference=self.ref,
                    job_path=self.root / "job.json", output_path=self.root / "video.mp4",
                    approved=True, estimated_cost_usd=.4)
        self.assertFalse((self.root / "job.json").exists())


if __name__ == "__main__":
    unittest.main()
