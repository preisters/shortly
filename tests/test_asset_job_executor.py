from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shortly.apps.personal.assets.fake import FakeImageProvider
from shortly.apps.personal.automation.executor import (
    AssetJobExecutor,
    _image_generation_prompt,
    _video_generation_prompt,
)
from shortly.apps.personal.automation.settings import PersonalAutomationSettings
from shortly.apps.personal.automation.store import (
    AutomationStore,
    BudgetExceededError,
)
from shortly.core.domain.assets import AssetType, CandidateStatus
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest


class AssetJobExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = PersonalAutomationSettings(
            state_dir=root / "state",
            artifact_dir=root / "artifacts",
            image_estimated_cost_usd=0.0,
        )
        self.store = AutomationStore(self.settings.database_path)
        self.provider = FakeImageProvider()
        self.executor = AssetJobExecutor(
            store=self.store,
            settings=self.settings,
            image_providers={"fake": self.provider},
            video_providers={},
        )
        self.manifest = build_cut_manifest(
            {
                "title": "fixture",
                "segments": [
                    {
                        "index": 0,
                        "duration_sec": 2,
                        "image_suggestions": ["생성 일러스트"],
                    }
                ],
            }
        )
        self.store.save_manifest(self.manifest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_approved_job_generates_prepares_selects_and_records_usage(self) -> None:
        job = self.executor.queue_cut(
            manifest_id=self.manifest.manifest_id,
            cut_id="s000-c00",
            provider="fake",
            model="fixture",
            estimated_cost_usd=0.02,
        )
        token = self.executor.approve(job["id"])
        completed = self.executor.execute(job["id"], token)

        self.assertEqual(completed["status"], "completed")
        manifest = self.store.load_manifest(self.manifest.manifest_id)
        candidate = manifest.cuts[0].selected_candidate
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.status, CandidateStatus.SELECTED)
        self.assertTrue(Path(candidate.original_path).is_file())
        self.assertTrue(Path(candidate.prepared_path).is_file())
        self.assertEqual(
            self.store.usage_summary(self.manifest.manifest_id)["image_usd"],
            0.02,
        )

    def test_budget_is_checked_before_approval(self) -> None:
        strict = PersonalAutomationSettings(
            state_dir=self.settings.state_dir,
            artifact_dir=self.settings.artifact_dir,
            image_budget_per_video_usd=0.01,
            image_estimated_cost_usd=0.0,
        )
        executor = AssetJobExecutor(
            store=self.store,
            settings=strict,
            image_providers={"fake": self.provider},
            video_providers={},
        )
        job = executor.queue_cut(
            manifest_id=self.manifest.manifest_id,
            cut_id="s000-c00",
            provider="fake",
            model="fixture",
            estimated_cost_usd=0.02,
        )
        with self.assertRaises(BudgetExceededError):
            executor.approve(job["id"])
        self.assertEqual(self.provider.calls, 0)

    def test_image_prompt_requests_reference_safe_vertical_composition(self) -> None:
        prompt = _image_generation_prompt("협상 장면")

        self.assertIn("vertical 2:3", prompt)
        self.assertIn("1024x1536", prompt)
        self.assertIn("safe margins", prompt)
        self.assertIn("Do not add captions", prompt)
        self.assertIn("charts, graphs", prompt)
        self.assertIn("geographic maps", prompt)

    def test_fictional_person_prompt_does_not_copy_real_identity(self) -> None:
        prompt = _image_generation_prompt(
            "가상의 기업 대표가 말하는 장면",
            disclosure_text="실존 인물이 아닙니다.",
        )

        self.assertIn("fictional adult", prompt)
        self.assertIn("does not resemble a specific real person", prompt)

    def test_video_prompt_requests_single_caption_safe_shot(self) -> None:
        prompt = _video_generation_prompt("수출 상자를 옮기는 장면")

        self.assertIn("vertical 9:16", prompt)
        self.assertIn("one clear action", prompt)
        self.assertIn("upper quarter calm", prompt)
        self.assertIn("Do not add text", prompt)

    def test_exact_data_visual_cannot_be_queued_for_generation(self) -> None:
        cut = self.manifest.cuts[0]
        cut.asset_type = AssetType.GENERATED_IMAGE
        cut.prompt = "2023년 매출 4억 달러 상승 그래프"
        self.store.save_manifest(self.manifest)

        with self.assertRaisesRegex(ValueError, "자동 생성할 수 없습니다"):
            self.executor.queue_cut(
                manifest_id=self.manifest.manifest_id,
                cut_id=cut.id,
                provider="fake",
                model="fixture",
                estimated_cost_usd=0.02,
            )


if __name__ == "__main__":
    unittest.main()
