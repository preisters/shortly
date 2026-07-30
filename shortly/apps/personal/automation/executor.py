"""Approved, budgeted execution of personal asset jobs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

from ....core.domain.assets import (
    ApprovalStatus,
    AssetCandidate,
    AssetType,
    CandidateStatus,
    MediaKind,
)
from ....core.use_cases.build_cut_manifest import (
    generated_visual_requires_exact_source,
)
from ....core.ports.assets import (
    AssetGenerationError,
    ImageGenerationProvider,
    ImageGenerationRequest,
    VideoGenerationProvider,
    VideoGenerationRequest,
    VideoSubmission,
)
from ..assets.postprocess import persist_original, prepare_image
from .budget import assert_budget
from .settings import PersonalAutomationSettings
from .store import AutomationStore


def _image_generation_prompt(
    prompt: str,
    *,
    disclosure_text: str = "",
) -> str:
    person_requirement = (
        "Any person must be a clearly fictional adult who does not resemble a "
        "specific real person. Depict only the role, emotion, and situation. "
        if disclosure_text
        else ""
    )
    return (
        f"{prompt.strip()}\n\n"
        "Composition requirements: create a vertical 2:3 image at 1024x1536. "
        "Keep the main subject centered with comfortable safe margins on every side "
        "so it can be placed inside a 9:16 Shorts frame without cutting off important "
        f"details. {person_requirement}"
        "Do not add captions, quotes, letters, numbers, logos, watermarks, borders, "
        "UI elements, data cards, charts, graphs, tables, documents, or geographic "
        "maps. The CapCut project adds all verified text separately."
    )


class AssetJobExecutor:
    def __init__(
        self,
        *,
        store: AutomationStore,
        settings: PersonalAutomationSettings,
        image_providers: Mapping[str, ImageGenerationProvider],
        video_providers: Mapping[str, VideoGenerationProvider],
    ):
        self.store = store
        self.settings = settings
        self.image_providers = dict(image_providers)
        self.video_providers = dict(video_providers)

    def queue_cut(
        self,
        *,
        manifest_id: str,
        cut_id: str,
        provider: str,
        model: str,
        estimated_cost_usd: float,
    ) -> dict:
        manifest = self.store.load_manifest(manifest_id)
        cut = manifest.cut(cut_id)
        if generated_visual_requires_exact_source(cut.prompt):
            raise ValueError(
                "지도·수치·그래프·정보 카드는 생성형 AI가 사실과 다른 내용을 "
                "만들 수 있어 자동 생성할 수 없습니다. 원본 자료나 정확하게 "
                "제작한 화면을 선택해 주세요."
            )
        if cut.fact_sensitive:
            raise ValueError(
                "Fact-sensitive cuts cannot use automatic generation."
            )
        if cut.asset_type is AssetType.GENERATED_IMAGE:
            kind = "image"
        elif cut.asset_type is AssetType.GENERATED_VIDEO:
            kind = "video"
        else:
            raise ValueError(
                f"Cut {cut_id} is not configured for generated media."
            )
        if provider not in (
            self.image_providers if kind == "image" else self.video_providers
        ):
            raise ValueError(f"Unknown {kind} provider: {provider}")
        configured_estimate = (
            self.settings.image_estimated_cost_usd
            if kind == "image"
            else self.settings.video_estimated_cost_usd
        )
        effective_estimate = max(
            0.0, float(estimated_cost_usd), configured_estimate
        )
        existing = self.store.list_jobs(manifest_id=manifest_id, limit=1000)
        generation_number = 1 + sum(
            1
            for job in existing
            if job["cut_id"] == cut_id and job["kind"] == kind
        )
        maximum_generations = 1 + self.settings.max_regenerations
        if generation_number > maximum_generations:
            raise ValueError(
                f"Cut has reached the generation limit ({maximum_generations})."
            )
        digest = hashlib.sha256(
            (
                f"{manifest_id}:{cut_id}:{kind}:{provider}:{model}:"
                f"{generation_number}:{cut.prompt}"
            ).encode("utf-8")
        ).hexdigest()
        return self.store.create_job(
            manifest_id=manifest_id,
            cut_id=cut_id,
            kind=kind,
            provider=provider,
            model=model,
            request={
                "prompt": cut.prompt,
                "generation": generation_number,
                "disclosure_text": cut.disclosure_text,
            },
            idempotency_key=digest,
            max_attempts=1,
            estimated_cost_usd=effective_estimate,
        )

    def approve(self, job_id: str) -> str:
        job = self.store.get_job(job_id)
        assert_budget(
            self.store,
            self.settings,
            manifest_id=job["manifest_id"],
            kind=job["kind"],
            estimated_cost_usd=job["estimated_cost_usd"],
        )
        token = self.store.approve_job(job_id)
        manifest = self.store.load_manifest(job["manifest_id"])
        manifest.cut(job["cut_id"]).approval_status = ApprovalStatus.APPROVED
        self.store.save_manifest(manifest)
        return token

    def execute(self, job_id: str, approval_token: str) -> dict:
        job = self.store.get_job(job_id)
        if job["status"] != "approved":
            raise ValueError(f"Job cannot run from {job['status']}")
        assert_budget(
            self.store,
            self.settings,
            manifest_id=job["manifest_id"],
            kind=job["kind"],
            estimated_cost_usd=job["estimated_cost_usd"],
        )
        self.store.consume_approval(job_id, approval_token)
        self.store.update_job(
            job_id, status="running", increment_attempts=True
        )
        try:
            if job["kind"] == "image":
                result = self._run_image(job)
            else:
                result = self._run_video(job)
        except (AssetGenerationError, OSError, ValueError) as exc:
            self._mark_failure(job, exc)
            raise
        if result.get("status") == "submitted":
            self.store.update_job(job_id, status="submitted", result=result)
            return self.store.get_job(job_id)
        return self._complete(job, result)

    def poll(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        if job["status"] != "submitted" or not job["result"]:
            raise ValueError(f"Job cannot be polled from {job['status']}")
        provider = self.video_providers[job["provider"]]
        prior = job["result"]["submission"]
        submission = VideoSubmission(
            id=prior["id"],
            provider=job["provider"],
            model=job["model"],
            completed=False,
            metadata=dict(prior.get("metadata") or {}),
        )
        try:
            updated = provider.poll(submission)
        except AssetGenerationError as exc:
            self._mark_failure(job, exc)
            raise
        if not updated.completed or updated.asset is None:
            result = {
                "status": "submitted",
                "submission": {
                    "id": updated.id,
                    "metadata": updated.metadata,
                },
            }
            self.store.update_job(job_id, status="submitted", result=result)
            return self.store.get_job(job_id)
        return self._complete(job, self._persist(job, updated.asset))

    def _run_image(self, job: dict) -> dict:
        request = ImageGenerationRequest(
            prompt=_image_generation_prompt(
                job["request"]["prompt"],
                disclosure_text=str(
                    job["request"].get("disclosure_text") or ""
                ),
            ),
            model=job["model"],
            size="1024x1536",
            idempotency_key=job["idempotency_key"],
        )
        asset = self.image_providers[job["provider"]].generate(request)
        return self._persist(job, asset)

    def _run_video(self, job: dict) -> dict:
        request = VideoGenerationRequest(
            prompt=job["request"]["prompt"],
            model=job["model"],
            idempotency_key=job["idempotency_key"],
        )
        submission = self.video_providers[job["provider"]].submit(request)
        if not submission.completed or submission.asset is None:
            return {
                "status": "submitted",
                "submission": {
                    "id": submission.id,
                    "metadata": submission.metadata,
                },
            }
        return self._persist(job, submission.asset)

    def _persist(self, job: dict, asset) -> dict:
        directory = (
            self.settings.assets_dir
            / job["manifest_id"]
            / job["cut_id"]
            / job["id"]
        )
        original = persist_original(asset, directory, job["kind"])
        prepared = original
        if asset.media_kind is MediaKind.IMAGE:
            prepared = prepare_image(original, directory / "prepared-1080x1920.jpg")
        actual_cost = (
            asset.cost_usd
            if asset.cost_usd > 0
            else float(job["estimated_cost_usd"])
        )
        candidate = AssetCandidate(
            provider=asset.provider,
            model=asset.model,
            media_kind=asset.media_kind,
            status=CandidateStatus.SELECTED,
            original_path=str(original),
            prepared_path=str(prepared),
            mime_type=asset.mime_type,
            external_id=asset.external_id,
            cost_usd=actual_cost,
            metadata=dict(asset.metadata),
        )
        return {
            "status": "completed",
            "candidate": candidate.to_dict(),
            "cost_usd": actual_cost,
        }

    def _complete(self, job: dict, result: dict) -> dict:
        candidate = AssetCandidate.from_dict(result["candidate"])
        manifest = self.store.load_manifest(job["manifest_id"])
        cut = manifest.cut(job["cut_id"])
        for existing in cut.candidates:
            if existing.status is CandidateStatus.SELECTED:
                existing.status = CandidateStatus.READY
        cut.candidates.append(candidate)
        cut.selected_candidate_id = candidate.id
        cut.approval_status = ApprovalStatus.APPROVED
        self.store.save_manifest(manifest)
        cost = float(result.get("cost_usd") or 0.0)
        self.store.record_usage(
            job_id=job["id"],
            manifest_id=job["manifest_id"],
            kind=job["kind"],
            provider=job["provider"],
            model=job["model"],
            cost_usd=cost,
        )
        self.store.record_selection(
            manifest_id=job["manifest_id"],
            cut_id=job["cut_id"],
            candidate_id=candidate.id,
            action="auto_selected",
            reason="Single generated candidate",
            metadata={"job_id": job["id"]},
        )
        self.store.update_job(
            job["id"],
            status="completed",
            result=result,
            actual_cost_usd=cost,
        )
        return self.store.get_job(job["id"])

    def _mark_failure(self, job: dict, exc: Exception) -> None:
        manifest = self.store.load_manifest(job["manifest_id"])
        cut = manifest.cut(job["cut_id"])
        cut.asset_type = AssetType.MANUAL_REQUIRED
        cut.approval_status = ApprovalStatus.REJECTED
        cut.notes.append(
            f"자동 생성 실패: {type(exc).__name__}. 수동 에셋이 필요합니다."
        )
        self.store.save_manifest(manifest)
        self.store.update_job(
            job["id"],
            status="manual_required",
            error=f"{type(exc).__name__}: {exc}",
        )
