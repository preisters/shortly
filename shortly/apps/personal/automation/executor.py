"""Approved, budgeted execution of personal asset jobs."""

from __future__ import annotations

import hashlib
import re
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


_VISIBLE_TEXT_MARKER = "화면 글자 지시:"
_VIDEO_OUTPUT_DURATION_SECONDS = 4
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|key)\s*[=:]\s*)[^\s,;&]+"),
)


def _safe_error_text(exc: Exception) -> str:
    value = f"{type(exc).__name__}: {exc}".strip()
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[마스킹]", value)
    return value[:800]


def _failure_payload(job: dict, exc: Exception) -> dict:
    """공급자 예외를 UI가 판단할 수 있는 안정적인 실패 정보로 정규화한다."""
    technical = _safe_error_text(exc)
    lowered = technical.lower()
    code = str(getattr(exc, "code", "") or "").strip()
    retryable = getattr(exc, "retryable", None)
    action = str(getattr(exc, "recommended_action", "") or "").strip()
    http_status = getattr(exc, "http_status", None)

    if not code:
        if any(token in lowered for token in ("api key", "unauthorized", "forbidden")):
            code = "authentication_failed"
        elif any(
            token in lowered
            for token in (
                "quota",
                "billing",
                "credit",
                "insufficient",
                "resource_exhausted",
                "purchase credits",
            )
        ):
            code = "quota_exceeded"
        elif any(token in lowered for token in ("rate limit", "too many requests")):
            code = "rate_limited"
        elif any(token in lowered for token in ("safety", "policy", "blocked")):
            code = "content_policy_rejected"
        elif any(token in lowered for token in ("timeout", "connection", "network")):
            code = "network_error"
        elif isinstance(exc, OSError):
            code = "local_io_error"
        elif isinstance(exc, ValueError):
            code = "invalid_response_or_request"
        else:
            code = "generation_failed"

    presentations = {
        "authentication_failed": (
            "API 인증 또는 사용 권한을 확인하지 못했습니다.",
            "API 키·프로젝트 권한·모델 접근 권한을 확인하고 서버를 재시작한 뒤 다시 생성하세요.",
            False,
            "check_settings",
            False,
        ),
        "provider_unavailable": (
            "선택한 생성 공급자를 현재 사용할 수 없습니다.",
            "API 키, 선택 의존성, 공급자와 모델 설정을 확인한 뒤 다시 생성하세요.",
            False,
            "check_settings",
            False,
        ),
        "quota_exceeded": (
            "상용 AI 토큰·크레딧 또는 사용 할당량이 부족합니다.",
            "공급자 대시보드에서 잔액과 사용 한도를 확인한 뒤 다시 생성하세요.",
            False,
            "check_billing",
            False,
        ),
        "rate_limited": (
            "짧은 시간에 요청이 몰려 공급자가 생성을 제한했습니다.",
            "잠시 기다린 뒤 다시 생성하는 것을 권장합니다.",
            True,
            "retry_later",
            False,
        ),
        "content_policy_rejected": (
            "프롬프트가 공급자의 안전 정책 또는 콘텐츠 필터에 의해 거부됐습니다.",
            "문구·실존 인물·민감한 표현을 완화해 다시 생성하고, 반복되면 수동 에셋으로 전환하세요.",
            False,
            "revise_prompt_or_manual",
            True,
        ),
        "invalid_request": (
            "모델이 현재 요청 형식·크기·옵션을 지원하지 않거나 프롬프트 요청이 유효하지 않습니다.",
            "모델 및 생성 설정을 확인하고 프롬프트를 수정한 뒤 다시 생성하세요.",
            False,
            "check_settings_or_prompt",
            False,
        ),
        "invalid_response_or_request": (
            "생성 응답을 해석하지 못했거나 요청 값이 유효하지 않습니다.",
            "상세 오류를 확인해 설정이나 프롬프트를 수정하고, 동일하면 수동 처리를 검토하세요.",
            False,
            "inspect_error",
            False,
        ),
        "invalid_provider_response": (
            "공급자가 정상적인 이미지·영상 데이터를 반환하지 않았습니다.",
            "일시적인 공급자 응답 오류일 수 있으므로 잠시 후 다시 생성하세요.",
            True,
            "retry_later",
            False,
        ),
        "provider_server_error": (
            "생성 공급자 서버에서 일시적인 오류가 발생했습니다.",
            "잠시 기다린 뒤 다시 생성하는 것을 권장합니다.",
            True,
            "retry_later",
            False,
        ),
        "network_error": (
            "생성 공급자와 통신하는 중 네트워크 오류가 발생했습니다.",
            "인터넷 연결을 확인하고 잠시 후 다시 생성하세요.",
            True,
            "retry_later",
            False,
        ),
        "local_io_error": (
            "생성 결과를 로컬 파일로 저장하거나 후처리하지 못했습니다.",
            "디스크 여유 공간·저장 경로·파일 권한을 확인한 뒤 다시 생성하세요.",
            True,
            "check_local_storage",
            False,
        ),
    }
    default_retryable = True if retryable is None else bool(retryable)
    reason, recommendation, mapped_retryable, mapped_action, manual = presentations.get(
        code,
        (
            "생성 작업이 완료되지 않았습니다.",
            "상세 오류를 확인한 뒤 한 번 재시도하고, 같은 오류가 반복되면 수동 처리를 검토하세요.",
            default_retryable,
            "retry_once_then_manual",
            False,
        ),
    )
    if retryable is not None:
        mapped_retryable = bool(retryable)
    if action:
        mapped_action = action
    return {
        "code": code,
        "reason": reason,
        "recommendation": recommendation,
        "recommended_action": mapped_action,
        "retryable": bool(mapped_retryable),
        # 개인 자동화판은 사용자가 중단할 때까지 재생성을 허용한다.
        # 공급자 오류의 retryable 여부는 안내 정보이며 버튼을 막지 않는다.
        "retry_available": True,
        "manual_recommended": manual,
        "provider": str(job.get("provider") or ""),
        "model": str(job.get("model") or ""),
        "kind": str(job.get("kind") or ""),
        "http_status": http_status,
        "technical_detail": technical,
    }


def _visible_text_requirement(prompt: str, *, medium: str) -> str:
    if _VISIBLE_TEXT_MARKER not in prompt:
        return (
            "Do not add captions, quotes, letters, numbers, logos, labels, "
            "watermarks, readable UI, signs, or text-like gibberish. "
        )
    directive = prompt.split(_VISIBLE_TEXT_MARKER, 1)[1].strip()
    return (
        "Visible-text contract: follow the Korean instruction after '화면 글자 지시:' "
        "literally. Render only the explicitly declared strings, with exactly the "
        "declared Korean spelling, placement, color, and typography. Every duplicate "
        f"product or object in the {medium} must use identical packaging and identical "
        "text. Do not invent, translate, mutate, or add any other letters, numbers, "
        "logos, pseudo-text, or gibberish. If the instruction says no readable text, "
        "show no text-like marks at all. "
        f"Declared instruction: {directive} "
    )


def _visual_fidelity_requirement() -> str:
    return (
        "Visual fidelity and reveal-order contract: use narration to resolve the "
        "scene's meaning and referents, not to depict every clause at once. Depict "
        "only the objects, colors, product versions and outcomes assigned to this "
        "specific cut; never reveal a later clause's colors or products early. "
        "A food scene about colored ketchup must visibly show the assigned colored "
        "ketchup on the food, not just an ordinary hotdog or red sauce. "
        "For an identifiable real product, preserve the verified reference's era, "
        "body silhouette, proportions, material, cap/nozzle and label placement. "
        "Never substitute a generic cylindrical squeeze bottle or invented package. "
        "A claim about physical design requires the matching real product detail. "
        "If no verified reference is supplied, do not invent that design; the cut "
        "needs a real photo or manual reference-based production. A brand name "
        "alone is not a visual reference, and attaching a reference is not proof "
        "that the generated result matches it. Compare the result against it.\n"
    )


def _image_generation_prompt(
    prompt: str,
    *,
    disclosure_text: str = "",
    narration: str = "",
) -> str:
    person_requirement = (
        "Any person must be a clearly fictional adult who does not resemble a "
        "specific real person. Depict only the role, emotion, and situation. "
        if disclosure_text
        else ""
    )
    return (
        f"{prompt.strip()}\n\n"
        f"Narration context (meaning only, never render as text): {narration.strip()}\n"
        f"{_visual_fidelity_requirement()}"
        "Composition requirements: create one coherent, cinematic editorial still, "
        "not a poster, advertisement, collage, infographic, or card-news design. "
        "Prefer believable documentary realism, natural lighting, physically "
        "consistent hands and objects, and one immediately readable visual action. "
        "Create a vertical 2:3 image at 1024x1536. The entire image will be fitted "
        "below the captions without cropping; do not reserve a large empty caption "
        "area inside the image. Keep the complete main subject and action clearly "
        "visible with comfortable safe margins on every side. Do not crop faces, "
        "hands, products, or the object that explains the scene. "
        f"{person_requirement}"
        f"{_visible_text_requirement(prompt, medium='image')}"
        "Do not add borders, UI elements, data cards, charts, graphs, tables, "
        "documents, or geographic maps. The CapCut project adds narration captions "
        "separately."
    )


def _video_generation_prompt(prompt: str, *, narration: str = "") -> str:
    return (
        f"{prompt.strip()}\n\n"
        f"Narration context (meaning only, never render as text or speech): {narration.strip()}\n"
        f"{_visual_fidelity_requirement()}"
        "Food steam policy (overrides decorative steam requests above): omit visible "
        "steam or vapor by default. Do not add steam for appetizing food styling, "
        "warm atmosphere, or merely because food was reheated. Microwaved gimbap "
        "must not emit decorative steam; convey reheating through the microwave "
        "door or removing the plate instead. Permit only faint, natural steam when "
        "showing the heat is indispensable to understanding this specific action "
        "and its narration, such as pouring hot water to prepare instant noodles. "
        "Food type alone is not permission. If that necessity is absent or unclear, "
        "omit steam. Never turn steam into smoke, foam, bubbling mayonnaise, or "
        "unrequested boiling. Preserve the other requested subjects and action.\n"
        "Timing contract: the final output must be exactly 4 seconds. Use the entire "
        "four-second duration as one continuous shot with naturally progressing "
        "motion from start to finish. Do not loop, duplicate motion, freeze, or pad "
        "the ending. "
        "Create a short vertical 9:16 documentary-style B-roll shot with one clear "
        "action, stable subject identity, realistic motion and lighting, and no "
        "scene change. The entire frame will be fitted below the captions without "
        "cropping. Do not reserve an empty caption area inside the video. Keep the "
        "complete subject and action visible with comfortable margins on all sides "
        "throughout the shot. "
        f"{_visible_text_requirement(prompt, medium='video')}"
        "Do not add UI, charts, maps, or card-news graphics. Generate silent video "
        "without speech, music, ambient sound, or sound effects."
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
                "narration": cut.narration,
                "generation": generation_number,
                "disclosure_text": cut.disclosure_text,
                "output_duration_sec": _VIDEO_OUTPUT_DURATION_SECONDS,
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
        except Exception as exc:  # noqa: BLE001 - 공급자/후처리 실패를 작업 상태로 보존
            return self._mark_failure(job, exc)
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
        except Exception as exc:  # noqa: BLE001 - 비동기 공급자 실패도 동일하게 보존
            return self._mark_failure(job, exc)
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
                narration=str(job["request"].get("narration") or ""),
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
            prompt=_video_generation_prompt(
                job["request"]["prompt"],
                narration=str(job["request"].get("narration") or ""),
            ),
            model=job["model"],
            duration_seconds=_VIDEO_OUTPUT_DURATION_SECONDS,
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

    def _mark_failure(self, job: dict, exc: Exception) -> dict:
        failure = _failure_payload(job, exc)
        manifest = self.store.load_manifest(job["manifest_id"])
        cut = manifest.cut(job["cut_id"])
        cut.approval_status = ApprovalStatus.REJECTED
        cut.notes.append(
            f"자동 생성 실패({failure['code']}): {failure['reason']} "
            f"{failure['recommendation']}"
        )
        self.store.save_manifest(manifest)
        self.store.update_job(
            job["id"],
            status="failed",
            result={"status": "failed", "failure": failure},
            error=f"{failure['reason']} {failure['recommendation']}",
        )
        return self.store.get_job(job["id"])
