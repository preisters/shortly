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
from shortly.core.ports.assets import AssetGenerationError, VideoSubmission
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest


class _FailingImageProvider:
    name = "failing"

    def __init__(self, error: AssetGenerationError):
        self.error = error

    def generate(self, request):
        raise self.error


class _CapturingVideoProvider:
    name = "capture"

    def submit(self, request):
        self.request = request
        return VideoSubmission(id="fixture", provider=self.name, model=request.model)


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

    def test_cut_generation_count_is_not_limited(self) -> None:
        jobs = [
            self.executor.queue_cut(
                manifest_id=self.manifest.manifest_id,
                cut_id="s000-c00",
                provider="fake",
                model="fixture",
                estimated_cost_usd=0.0,
            )
            for _ in range(5)
        ]

        self.assertEqual(
            [job["request"]["generation"] for job in jobs],
            [1, 2, 3, 4, 5],
        )

    def test_image_prompt_requests_reference_safe_vertical_composition(self) -> None:
        prompt = _image_generation_prompt("협상 장면")

        self.assertIn("vertical 2:3", prompt)
        self.assertIn("1024x1536", prompt)
        self.assertNotIn("upper 25 percent", prompt)
        self.assertIn("without cropping", prompt)
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
        self.assertIn("exactly 4 seconds", prompt)
        self.assertIn("one continuous shot", prompt)
        self.assertIn("naturally progressing motion", prompt)
        self.assertIn("Do not loop, duplicate motion, freeze", prompt)
        self.assertNotIn("replay the same action", prompt)
        self.assertNotIn("remaining time", prompt)
        self.assertNotIn("upper 30 percent", prompt)
        self.assertIn("without cropping", prompt)
        self.assertIn("Do not reserve an empty caption area", prompt)
        self.assertIn("text-like gibberish", prompt)
        self.assertIn("Generate silent video", prompt)

    def test_declared_packaging_text_is_preserved_without_gibberish(self) -> None:
        base = (
            "마트 진열대의 동일한 초록색 김 봉지 여덟 개. "
            "화면 글자 지시: 모든 봉지 중앙에 정확히 '김'만 표기"
        )

        image_prompt = _image_generation_prompt(base)
        video_prompt = _video_generation_prompt(base)

        for prompt in (image_prompt, video_prompt):
            self.assertIn("Visible-text contract", prompt)
            self.assertIn("identical packaging and identical text", prompt)
            self.assertIn("Do not invent, translate, mutate", prompt)
            self.assertIn("정확히 '김'만 표기", prompt)

    def test_video_steam_policy_defaults_off_but_allows_essential_heat(self) -> None:
        for description, narration in (
            ("전자레인지에서 꺼낸 김밥에 김이 모락모락", "전자레인지에 데우면 됨"),
            ("컵라면에 뜨거운 물을 붓는다", "뜨거운 물만 있으면 먹을 수 있었음"),
        ):
            with self.subTest(description=description):
                prompt = _video_generation_prompt(description, narration=narration)
                self.assertIn(narration, prompt)
                self.assertIn("overrides decorative steam requests above", prompt)
                self.assertIn("Microwaved gimbap must not emit decorative steam", prompt)
                self.assertIn("Permit only faint, natural steam", prompt)
                self.assertIn("pouring hot water to prepare instant noodles", prompt)
                self.assertIn("Food type alone is not permission", prompt)
                self.assertIn("absent or unclear, omit steam", prompt)

    def test_video_job_snapshots_narration_for_steam_decision(self) -> None:
        provider = _CapturingVideoProvider()
        self.executor.video_providers[provider.name] = provider
        cut = self.manifest.cuts[0]
        cut.asset_type = AssetType.GENERATED_VIDEO
        cut.prompt = "전자레인지에서 김밥 접시를 꺼내는 장면"
        cut.narration = "전자레인지에 데우기만 하면 됐음"
        self.store.save_manifest(self.manifest)
        job = self.executor.queue_cut(
            manifest_id=self.manifest.manifest_id, cut_id=cut.id,
            provider=provider.name, model="fixture", estimated_cost_usd=0.0,
        )
        self.assertEqual(job["request"]["narration"], cut.narration)
        # A subsequent edit must not change the already-approved job's context.
        cut.narration = "다른 대사"
        self.store.save_manifest(self.manifest)
        self.executor._run_video(job)
        self.assertIn("전자레인지에 데우기만 하면 됐음", provider.request.prompt)
        self.assertNotIn("다른 대사", provider.request.prompt)
        self.assertEqual(provider.request.duration_seconds, 4)
        # Jobs saved before narration snapshots were added still execute safely.
        del job["request"]["narration"]
        self.executor._run_video(job)
        self.assertIn("absent or unclear, omit steam", provider.request.prompt)

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

    def test_transient_generation_failure_is_saved_with_retry_guidance(self) -> None:
        provider = _FailingImageProvider(
            AssetGenerationError(
                "rate limit from provider; token sk-secret-value",
                code="rate_limited",
                retryable=True,
                recommended_action="retry_later",
                http_status=429,
            )
        )
        executor = AssetJobExecutor(
            store=self.store,
            settings=self.settings,
            image_providers={"failing": provider},
            video_providers={},
        )
        job = executor.queue_cut(
            manifest_id=self.manifest.manifest_id,
            cut_id="s000-c00",
            provider="failing",
            model="fixture",
            estimated_cost_usd=0.02,
        )
        token = executor.approve(job["id"])

        failed = executor.execute(job["id"], token)

        self.assertEqual(failed["status"], "failed")
        failure = failed["result"]["failure"]
        self.assertEqual(failure["code"], "rate_limited")
        self.assertTrue(failure["retryable"])
        self.assertTrue(failure["retry_available"])
        self.assertFalse(failure["manual_recommended"])
        self.assertIn("잠시 기다린 뒤", failure["recommendation"])
        self.assertNotIn("sk-secret-value", failure["technical_detail"])
        manifest = self.store.load_manifest(self.manifest.manifest_id)
        self.assertEqual(manifest.cuts[0].asset_type, AssetType.GENERATED_IMAGE)

    def test_policy_failure_recommends_prompt_change_or_manual_asset(self) -> None:
        provider = _FailingImageProvider(
            AssetGenerationError(
                "request blocked by content policy",
                code="content_policy_rejected",
                retryable=False,
                recommended_action="revise_prompt_or_manual",
            )
        )
        executor = AssetJobExecutor(
            store=self.store,
            settings=self.settings,
            image_providers={"failing": provider},
            video_providers={},
        )
        job = executor.queue_cut(
            manifest_id=self.manifest.manifest_id,
            cut_id="s000-c00",
            provider="failing",
            model="fixture",
            estimated_cost_usd=0.02,
        )
        token = executor.approve(job["id"])

        failed = executor.execute(job["id"], token)

        failure = failed["result"]["failure"]
        self.assertFalse(failure["retryable"])
        self.assertTrue(failure["manual_recommended"])
        self.assertIn("수동 에셋", failure["recommendation"])
        self.assertEqual(
            self.store.load_manifest(self.manifest.manifest_id).cuts[0].asset_type,
            AssetType.GENERATED_IMAGE,
        )

    def test_quota_failure_directly_reports_token_or_credit_shortage(self) -> None:
        provider = _FailingImageProvider(
            AssetGenerationError(
                "insufficient_quota",
                code="quota_exceeded",
                retryable=False,
                recommended_action="check_billing",
                http_status=429,
            )
        )
        executor = AssetJobExecutor(
            store=self.store,
            settings=self.settings,
            image_providers={"failing": provider},
            video_providers={},
        )
        job = executor.queue_cut(
            manifest_id=self.manifest.manifest_id,
            cut_id="s000-c00",
            provider="failing",
            model="fixture",
            estimated_cost_usd=0.02,
        )
        token = executor.approve(job["id"])

        failed = executor.execute(job["id"], token)

        failure = failed["result"]["failure"]
        self.assertEqual(failure["code"], "quota_exceeded")
        self.assertIn("토큰·크레딧", failure["reason"])
        self.assertIn("잔액과 사용 한도", failure["recommendation"])
        self.assertFalse(failure["retryable"])


if __name__ == "__main__":
    unittest.main()
