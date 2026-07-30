from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shortly.apps.personal.automation.graph import WorkflowRun, WorkflowStage
from shortly.apps.personal.automation.store import (
    ApprovalRequiredError,
    AutomationStore,
)
from shortly.core.use_cases.build_cut_manifest import build_cut_manifest


class PersonalAutomationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = AutomationStore(
            Path(self.temporary.name) / "automation.sqlite3"
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

    def _job(self) -> dict:
        return self.store.create_job(
            manifest_id=self.manifest.manifest_id,
            cut_id="s000-c00",
            kind="image",
            provider="fake",
            model="fixture",
            request={"prompt": "fixture"},
            idempotency_key="one",
            max_attempts=1,
            estimated_cost_usd=0.02,
        )

    def test_job_is_idempotent_and_approval_is_one_time(self) -> None:
        first = self._job()
        second = self._job()
        self.assertEqual(first["id"], second["id"])

        token = self.store.approve_job(first["id"])
        with self.assertRaises(ApprovalRequiredError):
            self.store.consume_approval(first["id"], "wrong")
        self.store.consume_approval(first["id"], token)
        with self.assertRaises(ApprovalRequiredError):
            self.store.consume_approval(first["id"], token)

    def test_usage_and_selection_history_are_persistent(self) -> None:
        job = self._job()
        self.store.record_usage(
            job_id=job["id"],
            manifest_id=self.manifest.manifest_id,
            kind="image",
            provider="fake",
            model="fixture",
            cost_usd=0.25,
        )
        self.store.record_selection(
            manifest_id=self.manifest.manifest_id,
            cut_id="s000-c00",
            candidate_id="candidate",
            action="selected",
            reason="fixture",
        )
        self.assertEqual(
            self.store.usage_summary(self.manifest.manifest_id)["total_usd"],
            0.25,
        )
        self.assertEqual(
            self.store.selection_history(self.manifest.manifest_id)[0]["action"],
            "selected",
        )

    def test_manifest_can_be_resumed_by_source_guide_hash(self) -> None:
        found = self.store.find_manifest_by_source_hash(
            self.manifest.source_guide_hash
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.manifest_id, self.manifest.manifest_id)
        self.assertIsNone(
            self.store.find_manifest_by_source_hash("not-a-known-hash")
        )

    def test_workflow_rejects_implicit_or_invalid_transitions(self) -> None:
        run = WorkflowRun()
        run.advance(WorkflowStage.SCRIPT)
        run.save(self.store)
        loaded = WorkflowRun.load(self.store, run.id)
        self.assertEqual(loaded.stage, WorkflowStage.SCRIPT)
        with self.assertRaises(ValueError):
            loaded.advance(WorkflowStage.EXPORT_COMMITTED)


if __name__ == "__main__":
    unittest.main()
