"""FastAPI routes available only from the personal composition root."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ....config import get_settings
from ....core.domain.assets import AssetType, MediaKind
from ....core.use_cases.build_cut_manifest import build_cut_manifest
from ....llm import get_llm
from ..assets import GoogleVideoProvider, OpenAIImageProvider
from ..local_llm import LOCAL_PROVIDER_NAMES
from .capcut_assets import commit_export, load_prepared_export, prepare_export
from .executor import AssetJobExecutor
from .graph import WorkflowRun, WorkflowStage
from .review import qwen_read_only_review, technical_review
from .selection import select_candidate
from .settings import PersonalAutomationSettings
from .store import (
    ApprovalRequiredError,
    AutomationStore,
    BudgetExceededError,
)


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ManifestRequest(BaseModel):
    guide: dict[str, Any]
    overrides: dict[str, str] = Field(default_factory=dict)


class SavedGuideManifestRequest(BaseModel):
    channel: str
    name: str


class CutTypeRequest(BaseModel):
    asset_type: str


class QueueRequest(BaseModel):
    provider: str
    model: str
    estimated_cost_usd: float


class ExecuteRequest(BaseModel):
    approval_token: str


class SelectionRequest(BaseModel):
    candidate_id: str
    reason: str = ""


class PrepareExportRequest(BaseModel):
    manifest_id: str
    guide: dict[str, Any]
    capcut_root: Optional[str] = None


class CommitExportRequest(BaseModel):
    plan_path: str


class WorkflowRequest(BaseModel):
    workflow_id: Optional[str] = None
    target: Optional[str] = None
    manifest_id: Optional[str] = None
    state: dict[str, Any] = Field(default_factory=dict)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (BudgetExceededError, ApprovalRequiredError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(
        status_code=502, detail=f"{type(exc).__name__}: {exc}"
    )


def create_automation_components(base_settings=None):
    base = base_settings or get_settings(reload=True)
    settings = PersonalAutomationSettings.load(
        state_dir=base.data_dir / "automation",
        artifact_dir=base.artifact_dir,
    )
    store = AutomationStore(settings.database_path)
    executor = AssetJobExecutor(
        store=store,
        settings=settings,
        image_providers={
            "openai": OpenAIImageProvider(base.openai_api_key),
        },
        video_providers={
            "google": GoogleVideoProvider(base.gemini_api_key),
        },
    )
    return settings, store, executor


def create_router(
    settings: PersonalAutomationSettings,
    store: AutomationStore,
    executor: AssetJobExecutor,
    *,
    service_api: Any,
) -> APIRouter:
    router = APIRouter()

    @router.get("/personal/assets", response_class=HTMLResponse)
    def asset_console() -> HTMLResponse:
        page = STATIC_DIR / "assets.html"
        if not page.is_file():
            return HTMLResponse("<h1>Shortly personal assets</h1>")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @router.get("/api/personal/automation/status")
    def status() -> dict:
        return {
            "schema": "shortly-personal-automation-v1",
            "image_provider": "openai",
            "image_model": settings.openai_image_model,
            "video_provider": "google",
            "video_model": settings.google_video_model,
            "budgets": {
                "image_per_video_usd": settings.image_budget_per_video_usd,
                "video_per_video_usd": settings.video_budget_per_video_usd,
                "daily_usd": settings.daily_budget_usd,
                "monthly_usd": settings.monthly_budget_usd,
            },
            "candidate_count": settings.candidate_count,
            "max_regenerations": settings.max_regenerations,
            "default_estimates_usd": {
                "image": settings.image_estimated_cost_usd,
                "video": settings.video_estimated_cost_usd,
            },
        }

    @router.post("/api/personal/manifests")
    def create_manifest(body: ManifestRequest) -> dict:
        try:
            manifest = build_cut_manifest(
                body.guide, overrides=body.overrides
            )
            store.save_manifest(manifest)
            return manifest.to_dict()
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/personal/manifests/from-guide")
    def create_manifest_from_saved_guide(
        body: SavedGuideManifestRequest,
    ) -> dict:
        try:
            saved = service_api.get_guide(body.channel, body.name)
            if saved is None:
                raise KeyError("저장된 가이드를 찾을 수 없습니다.")
            guide = saved.get("guide")
            if not isinstance(guide, dict) or not guide.get("segments"):
                raise ValueError("저장된 가이드에 유효한 컷 정보가 없습니다.")

            candidate = build_cut_manifest(guide)
            manifest = store.find_manifest_by_source_hash(
                candidate.source_guide_hash
            )
            reused = manifest is not None
            if manifest is None:
                manifest = candidate
                manifest.metadata.update(
                    {
                        "source_channel": body.channel,
                        "source_guide_name": body.name,
                    }
                )
                store.save_manifest(manifest)
            return {
                "manifest": manifest.to_dict(),
                "guide": guide,
                "reused": reused,
            }
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/personal/manifests")
    def list_manifests() -> list[dict]:
        return store.list_manifests()

    @router.get("/api/personal/manifests/{manifest_id}")
    def get_manifest(manifest_id: str) -> dict:
        try:
            return store.load_manifest(manifest_id).to_dict()
        except Exception as exc:
            raise _error(exc) from exc

    @router.patch("/api/personal/manifests/{manifest_id}/cuts/{cut_id}")
    def set_cut_type(
        manifest_id: str, cut_id: str, body: CutTypeRequest
    ) -> dict:
        try:
            manifest = store.load_manifest(manifest_id)
            cut = manifest.cut(cut_id)
            target = AssetType(body.asset_type)
            if cut.fact_sensitive and target in (
                AssetType.GENERATED_IMAGE,
                AssetType.GENERATED_VIDEO,
            ):
                raise ValueError(
                    "Fact-sensitive cuts cannot use automatic generation."
                )
            cut.asset_type = target
            cut.media_kind = (
                MediaKind.VIDEO
                if target is AssetType.GENERATED_VIDEO
                else MediaKind.IMAGE
            )
            store.save_manifest(manifest)
            store.record_selection(
                manifest_id=manifest_id,
                cut_id=cut_id,
                candidate_id=None,
                action="asset_type_changed",
                metadata={"asset_type": target.value},
            )
            return cut.to_dict()
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/personal/manifests/{manifest_id}/cuts/{cut_id}/jobs")
    def queue_job(
        manifest_id: str, cut_id: str, body: QueueRequest
    ) -> dict:
        try:
            return executor.queue_cut(
                manifest_id=manifest_id,
                cut_id=cut_id,
                provider=body.provider,
                model=body.model,
                estimated_cost_usd=body.estimated_cost_usd,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/personal/jobs")
    def jobs(manifest_id: Optional[str] = None) -> list[dict]:
        return store.list_jobs(manifest_id=manifest_id)

    @router.post("/api/personal/jobs/{job_id}/approve")
    def approve_job(job_id: str) -> dict:
        try:
            return {
                "job_id": job_id,
                "approval_token": executor.approve(job_id),
                "warning": "This token is shown once and is consumed by execution.",
            }
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/personal/jobs/{job_id}/execute")
    def execute_job(job_id: str, body: ExecuteRequest) -> dict:
        try:
            return executor.execute(job_id, body.approval_token)
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/personal/jobs/{job_id}/poll")
    def poll_job(job_id: str) -> dict:
        try:
            return executor.poll(job_id)
        except Exception as exc:
            raise _error(exc) from exc

    @router.post(
        "/api/personal/manifests/{manifest_id}/cuts/{cut_id}/select"
    )
    def choose_candidate(
        manifest_id: str, cut_id: str, body: SelectionRequest
    ) -> dict:
        try:
            return select_candidate(
                store,
                manifest_id=manifest_id,
                cut_id=cut_id,
                candidate_id=body.candidate_id,
                reason=body.reason,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/personal/usage")
    def usage(manifest_id: Optional[str] = None) -> dict:
        return store.usage_summary(manifest_id)

    @router.post("/api/personal/manifests/{manifest_id}/review")
    def review(manifest_id: str, local_qwen: bool = False) -> dict:
        try:
            manifest = store.load_manifest(manifest_id)
            baseline = technical_review(manifest)
            if not local_qwen:
                return baseline.to_dict()
            base = get_settings()
            if base.llm_provider not in LOCAL_PROVIDER_NAMES:
                raise ValueError(
                    "Local Qwen must be selected as the analysis provider."
                )
            return qwen_read_only_review(
                get_llm(base), manifest, technical=baseline
            ).to_dict()
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/personal/exports/prepare")
    def prepare(body: PrepareExportRequest) -> dict:
        try:
            return prepare_export(
                store=store,
                manifest_id=body.manifest_id,
                guide=body.guide,
                staging_root=settings.assets_dir,
                capcut_root=body.capcut_root,
            ).to_dict()
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/personal/exports/commit")
    def commit(body: CommitExportRequest) -> dict:
        try:
            plan = Path(body.plan_path).resolve()
            allowed = settings.assets_dir.resolve()
            if allowed not in plan.parents:
                raise ValueError("Prepared export must be inside the asset directory.")
            return commit_export(load_prepared_export(plan))
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/personal/workflows")
    def workflow(body: WorkflowRequest) -> dict:
        try:
            run = (
                WorkflowRun.load(store, body.workflow_id)
                if body.workflow_id
                else WorkflowRun(manifest_id=body.manifest_id)
            )
            if body.target:
                run.advance(WorkflowStage(body.target), **body.state)
            elif body.state:
                run.state.update(body.state)
            if body.manifest_id:
                run.manifest_id = body.manifest_id
            run.save(store)
            return {
                "id": run.id,
                "manifest_id": run.manifest_id,
                "stage": run.stage.value,
                "state": run.state,
            }
        except Exception as exc:
            raise _error(exc) from exc

    return router
