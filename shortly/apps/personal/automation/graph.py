"""Deterministic workflow graph with explicit human/cost gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from .store import AutomationStore


class WorkflowStage(str, Enum):
    TOPIC = "topic"
    RESEARCH = "research"
    SCRIPT = "script"
    CUT_MANIFEST = "cut_manifest"
    COST_APPROVAL = "cost_approval"
    ASSET_GENERATION = "asset_generation"
    REVIEW = "review"
    EXPORT_PREPARED = "export_prepared"
    EXPORT_COMMITTED = "export_committed"
    COMPLETE = "complete"
    MANUAL_REQUIRED = "manual_required"


_TRANSITIONS = {
    WorkflowStage.TOPIC: {WorkflowStage.RESEARCH, WorkflowStage.SCRIPT},
    WorkflowStage.RESEARCH: {WorkflowStage.SCRIPT, WorkflowStage.MANUAL_REQUIRED},
    WorkflowStage.SCRIPT: {WorkflowStage.CUT_MANIFEST},
    WorkflowStage.CUT_MANIFEST: {
        WorkflowStage.COST_APPROVAL,
        WorkflowStage.REVIEW,
    },
    WorkflowStage.COST_APPROVAL: {
        WorkflowStage.ASSET_GENERATION,
        WorkflowStage.MANUAL_REQUIRED,
    },
    WorkflowStage.ASSET_GENERATION: {
        WorkflowStage.COST_APPROVAL,
        WorkflowStage.REVIEW,
        WorkflowStage.MANUAL_REQUIRED,
    },
    WorkflowStage.REVIEW: {
        WorkflowStage.EXPORT_PREPARED,
        WorkflowStage.COST_APPROVAL,
        WorkflowStage.MANUAL_REQUIRED,
    },
    WorkflowStage.EXPORT_PREPARED: {
        WorkflowStage.EXPORT_COMMITTED,
        WorkflowStage.REVIEW,
    },
    WorkflowStage.EXPORT_COMMITTED: {
        WorkflowStage.COMPLETE,
        WorkflowStage.REVIEW,
    },
    WorkflowStage.MANUAL_REQUIRED: {
        WorkflowStage.REVIEW,
        WorkflowStage.COST_APPROVAL,
    },
    WorkflowStage.COMPLETE: set(),
}


@dataclass
class WorkflowRun:
    stage: WorkflowStage = WorkflowStage.TOPIC
    id: str = field(default_factory=lambda: uuid4().hex)
    manifest_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)

    def advance(self, target: WorkflowStage, **state_updates: Any) -> None:
        target = WorkflowStage(target)
        if target not in _TRANSITIONS[self.stage]:
            raise ValueError(
                f"Invalid workflow transition: {self.stage.value} -> {target.value}"
            )
        self.stage = target
        self.state.update(state_updates)

    def save(self, store: AutomationStore) -> None:
        store.save_workflow(
            workflow_id=self.id,
            manifest_id=self.manifest_id,
            stage=self.stage.value,
            state=self.state,
        )

    @classmethod
    def load(cls, store: AutomationStore, workflow_id: str) -> "WorkflowRun":
        value = store.load_workflow(workflow_id)
        return cls(
            id=value["id"],
            manifest_id=value["manifest_id"],
            stage=WorkflowStage(value["stage"]),
            state=value["state"],
        )
