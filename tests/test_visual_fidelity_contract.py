from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from shortly.apps.personal.assets.fake import FakeImageProvider
from shortly.apps.personal.automation.settings import PersonalAutomationSettings
from shortly.apps.personal.automation.store import AutomationStore
from shortly.apps.personal.automation.executor import (
    AssetJobExecutor, _image_generation_prompt, _video_generation_prompt,
)
from shortly.apps.personal.automation.review import qwen_read_only_review
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest
from shortly.generate.image_reco import _recommend_llm


class _CaptureLlm:
    available = True
    text_model = "fixture"

    def complete_json(self, system, user, **kwargs):
        self.system, self.user = system, user
        return {"passed": True, "summary": "ok", "findings": [], "segments": []}


class VisualFidelityContractTests(unittest.TestCase):
    def test_batch_has_full_story_context_without_expanding_requested_batch(self):
        llm = _CaptureLlm()
        segments = [
            {"index": 0, "narration": "초록색 케첩을 출시했다", "cuts": 1},
            {"index": 1, "narration": "이걸 핫도그에 그렸다", "cuts": 1},
            {"index": 2, "narration": "나중에 파랑과 주황이 나왔다", "cuts": 1},
        ]
        _recommend_llm(llm, {}, {}, {"segments": segments}, segments=[segments[1]])
        context, requested = llm.user.split("[세그먼트]", 1)
        self.assertIn("초록색 케첩을 출시했다", context)
        self.assertIn("나중에 파랑과 주황이 나왔다", context)
        self.assertIn("- index 1: 컷", requested)
        self.assertNotIn("- index 0: 컷", requested)
        self.assertIn("경계 이후 내용을 앞 컷에 미리 보여주지 않는다", requested)
        self.assertIn("현재 보여줄 것:", requested)
        self.assertIn("아직 보여주지 않을 것:", requested)
        self.assertIn("real_image / fact_sensitive=true", requested)
        self.assertIn("실물 대조", requested)

    def test_both_media_prompts_preserve_context_product_and_reveal_order(self):
        narration = "초록색 케첩으로 핫도그에 그림을 그렸다"
        for builder in (_image_generation_prompt, _video_generation_prompt):
            with self.subTest(builder=builder.__name__):
                prompt = builder("현재 초록만 보여줄 것", narration=narration)
                self.assertIn(narration, prompt)
                self.assertIn("never reveal a later clause", prompt)
                self.assertIn("generic cylindrical squeeze bottle", prompt)
                self.assertIn("matching real product detail", prompt)
                self.assertIn("not just an ordinary hotdog", prompt)

    def test_plan_review_does_not_certify_unseen_pixels(self):
        llm = _CaptureLlm()
        manifest = build_cut_manifest({"title": "fixture", "segments": []})
        qwen_read_only_review(llm, manifest)
        self.assertIn("have not inspected image pixels", llm.system)
        self.assertIn("product_fidelity", llm.user)
        self.assertIn("context_mismatch", llm.user)
        self.assertIn("premature_reveal", llm.user)


class ImageNarrationHandoffTests(unittest.TestCase):
    def test_image_provider_receives_saved_narration(self):
        outer = self
        fake = FakeImageProvider()

        class Provider:
            def generate(self, request):
                outer.assertIn("초록색 케첩으로 그린 핫도그", request.prompt)
                return fake.generate(request)

        with tempfile.TemporaryDirectory() as folder:
            settings = PersonalAutomationSettings(
                state_dir=Path(folder) / "state", artifact_dir=Path(folder) / "artifacts",
                image_estimated_cost_usd=0,
            )
            store = AutomationStore(settings.database_path)
            manifest = build_cut_manifest({"title": "fixture", "segments": [{
                "index": 0, "duration_sec": 2, "narration": "초록색 케첩으로 그린 핫도그",
                "image_suggestions": ["생성 일러스트"],
            }]})
            store.save_manifest(manifest)
            executor = AssetJobExecutor(
                store=store, settings=settings,
                image_providers={"fake": Provider()}, video_providers={},
            )
            job = executor.queue_cut(
                manifest_id=manifest.manifest_id, cut_id="s000-c00",
                provider="fake", model="fixture", estimated_cost_usd=0,
            )
            result = executor.execute(job["id"], executor.approve(job["id"]))
            self.assertEqual(result["status"], "completed")
