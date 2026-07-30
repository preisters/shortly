"""SQLite persistence for jobs, approvals, usage, and human selections."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from ....core.domain.assets import CutManifest


class BudgetExceededError(RuntimeError):
    pass


class ApprovalRequiredError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class AutomationStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS manifests (
                    manifest_id TEXT PRIMARY KEY,
                    document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    manifest_id TEXT NOT NULL,
                    cut_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 2,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    actual_cost_usd REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_manifest
                    ON jobs(manifest_id, cut_id);
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    approved_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    manifest_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    units REAL NOT NULL DEFAULT 1,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_created
                    ON usage_events(created_at);
                CREATE TABLE IF NOT EXISTS selection_events (
                    id TEXT PRIMARY KEY,
                    manifest_id TEXT NOT NULL,
                    cut_id TEXT NOT NULL,
                    candidate_id TEXT,
                    action TEXT NOT NULL,
                    reason TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    manifest_id TEXT,
                    stage TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def save_manifest(self, manifest: CutManifest) -> None:
        now = _now()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO manifests(manifest_id, document_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(manifest_id) DO UPDATE SET
                    document_json=excluded.document_json,
                    updated_at=excluded.updated_at
                """,
                (manifest.manifest_id, _json(manifest.to_dict()), now, now),
            )

    def load_manifest(self, manifest_id: str) -> CutManifest:
        with self._connection() as db:
            row = db.execute(
                "SELECT document_json FROM manifests WHERE manifest_id=?",
                (manifest_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown manifest: {manifest_id}")
        return CutManifest.from_dict(json.loads(row["document_json"]))

    def list_manifests(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT manifest_id, document_json, created_at, updated_at
                FROM manifests ORDER BY updated_at DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [
            {
                "manifest_id": row["manifest_id"],
                "title": json.loads(row["document_json"]).get("title", ""),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def find_manifest_by_source_hash(
        self, source_guide_hash: str
    ) -> Optional[CutManifest]:
        """Return the most recently updated manifest for an identical guide."""

        if not source_guide_hash:
            return None
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT document_json
                FROM manifests
                ORDER BY updated_at DESC
                """
            ).fetchall()
        for row in rows:
            value = json.loads(row["document_json"])
            if value.get("source_guide_hash") == source_guide_hash:
                return CutManifest.from_dict(value)
        return None

    def create_job(
        self,
        *,
        manifest_id: str,
        cut_id: str,
        kind: str,
        provider: str,
        model: str,
        request: dict[str, Any],
        idempotency_key: str,
        max_attempts: int,
        estimated_cost_usd: float,
    ) -> dict[str, Any]:
        now = _now()
        job_id = uuid4().hex
        with self._connection() as db:
            existing = db.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            db.execute(
                """
                INSERT INTO jobs(
                    id, idempotency_key, manifest_id, cut_id, kind,
                    provider, model, status, request_json, max_attempts,
                    estimated_cost_usd, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_approval', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    manifest_id,
                    cut_id,
                    kind,
                    provider,
                    model,
                    _json(request),
                    max(1, max_attempts),
                    max(0.0, estimated_cost_usd),
                    now,
                    now,
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown job: {job_id}")
        value = dict(row)
        value["request"] = json.loads(value.pop("request_json"))
        result_json = value.pop("result_json")
        value["result"] = json.loads(result_json) if result_json else None
        return value

    def list_jobs(
        self, *, manifest_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = "SELECT id FROM jobs"
        params: list[Any] = []
        if manifest_id:
            query += " WHERE manifest_id=?"
            params.append(manifest_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self.get_job(row["id"]) for row in rows]

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        result: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        actual_cost_usd: Optional[float] = None,
        increment_attempts: bool = False,
    ) -> None:
        fields = ["status=?", "result_json=?", "error=?", "updated_at=?"]
        params: list[Any] = [
            status,
            _json(result) if result is not None else None,
            error,
            _now(),
        ]
        if actual_cost_usd is not None:
            fields.append("actual_cost_usd=?")
            params.append(max(0.0, actual_cost_usd))
        if increment_attempts:
            fields.append("attempts=attempts+1")
        params.append(job_id)
        with self._connection() as db:
            cursor = db.execute(
                f"UPDATE jobs SET {', '.join(fields)} WHERE id=?",
                params,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown job: {job_id}")

    def approve_job(self, job_id: str) -> str:
        job = self.get_job(job_id)
        if job["status"] not in ("awaiting_approval", "approved"):
            raise ValueError(f"Job cannot be approved from {job['status']}")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO approvals(id, job_id, token_hash, approved_at)
                VALUES (?, ?, ?, ?)
                """,
                (uuid4().hex, job_id, token_hash, _now()),
            )
            db.execute(
                "UPDATE jobs SET status='approved', updated_at=? WHERE id=?",
                (_now(), job_id),
            )
        return token

    def consume_approval(self, job_id: str, token: str) -> None:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connection() as db:
            row = db.execute(
                """
                SELECT id FROM approvals
                WHERE job_id=? AND token_hash=? AND consumed_at IS NULL
                """,
                (job_id, token_hash),
            ).fetchone()
            if row is None:
                raise ApprovalRequiredError(
                    "A valid, unused approval is required for this paid operation."
                )
            db.execute(
                "UPDATE approvals SET consumed_at=? WHERE id=?",
                (_now(), row["id"]),
            )

    def record_usage(
        self,
        *,
        job_id: str,
        manifest_id: str,
        kind: str,
        provider: str,
        model: str,
        cost_usd: float,
        units: float = 1.0,
    ) -> None:
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO usage_events(
                    id, job_id, manifest_id, kind, provider, model,
                    units, cost_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    job_id,
                    manifest_id,
                    kind,
                    provider,
                    model,
                    units,
                    max(0.0, cost_usd),
                    _now(),
                ),
            )

    def usage_summary(self, manifest_id: Optional[str] = None) -> dict[str, float]:
        where = ""
        params: list[Any] = []
        if manifest_id:
            where = " WHERE manifest_id=?"
            params.append(manifest_id)
        with self._connection() as db:
            total = db.execute(
                f"SELECT COALESCE(SUM(cost_usd), 0) AS value FROM usage_events{where}",
                params,
            ).fetchone()["value"]
            by_kind = db.execute(
                f"""
                SELECT kind, COALESCE(SUM(cost_usd), 0) AS value
                FROM usage_events{where} GROUP BY kind
                """,
                params,
            ).fetchall()
        result = {"total_usd": float(total or 0.0)}
        result.update(
            {f"{row['kind']}_usd": float(row["value"] or 0.0) for row in by_kind}
        )
        return result

    def spending_since(self, iso_timestamp: str) -> float:
        with self._connection() as db:
            row = db.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0) AS value
                FROM usage_events WHERE created_at >= ?
                """,
                (iso_timestamp,),
            ).fetchone()
        return float(row["value"] or 0.0)

    def record_selection(
        self,
        *,
        manifest_id: str,
        cut_id: str,
        candidate_id: Optional[str],
        action: str,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO selection_events(
                    id, manifest_id, cut_id, candidate_id, action,
                    reason, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    manifest_id,
                    cut_id,
                    candidate_id,
                    action,
                    reason,
                    _json(metadata or {}),
                    _now(),
                ),
            )

    def selection_history(
        self, manifest_id: str, cut_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM selection_events WHERE manifest_id=?"
        params: list[Any] = [manifest_id]
        if cut_id:
            query += " AND cut_id=?"
            params.append(cut_id)
        query += " ORDER BY created_at"
        with self._connection() as db:
            rows = db.execute(query, params).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["metadata"] = json.loads(value.pop("metadata_json"))
            values.append(value)
        return values

    def save_workflow(
        self,
        *,
        workflow_id: str,
        stage: str,
        state: dict[str, Any],
        manifest_id: Optional[str] = None,
    ) -> None:
        now = _now()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO workflow_runs(
                    id, manifest_id, stage, state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    manifest_id=excluded.manifest_id,
                    stage=excluded.stage,
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                (
                    workflow_id,
                    manifest_id,
                    stage,
                    _json(state),
                    now,
                    now,
                ),
            )

    def load_workflow(self, workflow_id: str) -> dict[str, Any]:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM workflow_runs WHERE id=?", (workflow_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown workflow: {workflow_id}")
        value = dict(row)
        value["state"] = json.loads(value.pop("state_json"))
        return value
