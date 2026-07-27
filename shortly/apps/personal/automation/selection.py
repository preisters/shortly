"""Human candidate selection and replacement history."""

from __future__ import annotations

from ....core.domain.assets import CandidateStatus
from .store import AutomationStore


def select_candidate(
    store: AutomationStore,
    *,
    manifest_id: str,
    cut_id: str,
    candidate_id: str,
    reason: str = "",
) -> dict:
    manifest = store.load_manifest(manifest_id)
    cut = manifest.cut(cut_id)
    selected = None
    for candidate in cut.candidates:
        if candidate.id == candidate_id:
            selected = candidate
            candidate.status = CandidateStatus.SELECTED
        elif candidate.status is CandidateStatus.SELECTED:
            candidate.status = CandidateStatus.READY
    if selected is None:
        raise KeyError(f"Unknown candidate: {candidate_id}")
    cut.selected_candidate_id = selected.id
    store.save_manifest(manifest)
    store.record_selection(
        manifest_id=manifest_id,
        cut_id=cut_id,
        candidate_id=candidate_id,
        action="selected",
        reason=reason,
    )
    return selected.to_dict()
