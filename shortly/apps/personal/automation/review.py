"""Read-only technical and local-Qwen review reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ....core.domain.assets import CutManifest
from ....generate.editorial import WHOLE_VIDEO_REVIEW_GUIDANCE
from ....llm.base import (
    LLMClient,
    LLMError,
    LLMResponseParseError,
    LLMUnavailable,
)


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    code: str
    message: str
    cut_id: str | None = None


@dataclass(frozen=True)
class ReviewReport:
    passed: bool
    findings: list[ReviewFinding] = field(default_factory=list)
    summary: str = ""
    reviewer: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [asdict(item) for item in self.findings],
            "summary": self.summary,
            "reviewer": self.reviewer,
        }


def technical_review(manifest: CutManifest) -> ReviewReport:
    findings: list[ReviewFinding] = []
    for cut in manifest.cuts:
        candidate = cut.selected_candidate
        if candidate is None:
            findings.append(
                ReviewFinding(
                    "warning",
                    "asset_missing",
                    "선택된 에셋이 없어 플레이스홀더 또는 수동 삽입이 필요합니다.",
                    cut.id,
                )
            )
            continue
        path = Path(candidate.prepared_path or candidate.original_path or "")
        if not path.is_file():
            findings.append(
                ReviewFinding(
                    "error",
                    "file_missing",
                    "선택된 에셋 파일을 찾을 수 없습니다.",
                    cut.id,
                )
            )
        if candidate.cost_usd < 0:
            findings.append(
                ReviewFinding(
                    "error",
                    "invalid_cost",
                    "에셋 비용 기록이 올바르지 않습니다.",
                    cut.id,
                )
            )
    return ReviewReport(
        passed=not any(item.severity == "error" for item in findings),
        findings=findings,
        summary=f"{len(manifest.cuts)}개 컷, {len(findings)}개 확인 항목",
    )


def qwen_read_only_review(
    llm: LLMClient,
    manifest: CutManifest,
    *,
    technical: ReviewReport | None = None,
) -> ReviewReport:
    """Ask a local model for feedback; no mutation object is provided."""

    if not getattr(llm, "available", False):
        return ReviewReport(
            passed=False,
            findings=[
                ReviewFinding(
                    "warning",
                    "local_model_unavailable",
                    "로컬 검수 모델을 사용할 수 없습니다.",
                )
            ],
            summary="로컬 검수를 실행하지 못했습니다.",
            reviewer="local-qwen",
        )
    snapshot = manifest.to_dict()
    for cut in snapshot["cuts"]:
        for candidate in cut.get("candidates", []):
            candidate.pop("original_path", None)
            candidate.pop("prepared_path", None)
    system = (
        "You review a YouTube Shorts cut plan. Return feedback only. "
        "Never propose or claim that you changed files, the manifest, or a project. "
        "This is a text-only plan review: you have not inspected image pixels. "
        "Never certify a generated product's visual accuracy from its prompt alone. "
        "Be concise, return at most 6 findings, and keep each message under 120 characters."
    )
    user = (
        "다음 컷 구성을 검수하세요. JSON만 반환하세요. "
        '형식: {"passed": bool, "summary": str, "findings": '
        '[{"severity":"info|warning|error","code":str,'
        '"message":str,"cut_id":str|null}]}\n'
        "각 컷을 앞뒤 대사와 함께 검수한다. 실존 제품의 시대·버전·실루엣·뚜껑·라벨이 "
        "요구되는 컷은 원본/참조 확보 계획을 확인하고, 외형 설명에 무지 용기나 임의 상품을 "
        "대신 쓰면 product_fidelity 경고를 낸다. 지시어와 수식어가 화면에 구현되는지도 확인한다 "
        "(컬러 케첩으로 그렸다는 대사에 평범한 핫도그만 있으면 context_mismatch). "
        "다음 절의 색상·제품·결과를 앞 컷에 공개하면 premature_reveal 경고를 낸다. "
        "실제 생성 이미지의 실물 대조는 별도 시각 검수가 필요하며 이 텍스트 검수로 대체하지 않는다.\n"
        f"{WHOLE_VIDEO_REVIEW_GUIDANCE}\n"
        "전체 순서에서 광고 인상은 advertorial_impression, 도입 뒤 시청 이유 부족은 "
        "missing_post_hook_reason, 제목과 중간 관심사의 이탈은 story_detour로 구체적 구간과 함께 보고한다. "
        "주요 전개 문제를 기술 검사 통과로 덮지 않는다. 단어가 등장했다는 이유만으로 자동 불합격시키지 말고 "
        "제공된 대사·화면 계획에서 실제 문제를 특정한다. 전체 흐름 문제에는 cut_id=null을 쓸 수 있다.\n"
        f"manifest={json.dumps(snapshot, ensure_ascii=False)}\n"
        f"technical={json.dumps((technical or technical_review(manifest)).to_dict(), ensure_ascii=False)}"
    )
    try:
        value = llm.complete_json(system, user, temperature=0.1, max_tokens=800)
        findings = [
            ReviewFinding(
                severity=str(item.get("severity") or "warning"),
                code=str(item.get("code") or "model_feedback"),
                message=str(item.get("message") or ""),
                cut_id=item.get("cut_id"),
            )
            for item in (value.get("findings") or [])
            if isinstance(item, dict)
        ]
        return ReviewReport(
            passed=bool(value.get("passed", False)),
            findings=findings,
            summary=str(value.get("summary") or ""),
            reviewer=f"local-qwen:{llm.text_model}",
        )
    except LLMResponseParseError as exc:
        return ReviewReport(
            passed=False,
            findings=[
                ReviewFinding(
                    "warning",
                    "review_parse_failed",
                    f"로컬 검수 응답을 해석하지 못했습니다: {type(exc).__name__}",
                )
            ],
            summary="로컬 검수 결과 해석 실패",
            reviewer=f"local-qwen:{llm.text_model}",
        )
    except (LLMUnavailable, LLMError) as exc:
        auto_start = bool(getattr(llm, "_auto_start", False))
        return ReviewReport(
            passed=False,
            findings=[
                ReviewFinding(
                    "warning",
                    "local_model_request_failed",
                    (
                        (
                            "LM Studio 자동 시작 또는 검수 모델 자동 로드에 실패했습니다. "
                            "LM Studio CLI와 로컬 모델 설치 상태를 확인하세요. "
                            if auto_start
                            else
                            "로컬 모델 호출에 실패했습니다. LM Studio 서버가 "
                            "설정된 주소에서 실행 중인지, 검수 모델이 로드됐는지 확인하세요. "
                        )
                        + f"오류 유형: {type(exc).__name__}"
                    ),
                )
            ],
            summary="로컬 검수 모델 호출 실패",
            reviewer=f"local-qwen:{llm.text_model}",
        )
    except (ValueError, TypeError) as exc:
        return ReviewReport(
            passed=False,
            findings=[
                ReviewFinding(
                    "warning",
                    "review_schema_invalid",
                    (
                        "로컬 검수 JSON의 필드 형식이 올바르지 않습니다: "
                        f"{type(exc).__name__}"
                    ),
                )
            ],
            summary="로컬 검수 응답 스키마 오류",
            reviewer=f"local-qwen:{llm.text_model}",
        )
