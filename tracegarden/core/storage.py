"""
tracegarden.core.storage
~~~~~~~~~~~~~~~~~~~~~~~~
SQLite storage backend for TraceGarden.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from .models import CeleryTask, TraceRequest


_DEFAULT_DB_PATH = "/tmp/tracegarden.db"


class TraceStorage:
    """Thread-safe SQLite storage for TraceRequest and CeleryTask records."""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH, max_requests: int = 5000):
        self.db_path = str(db_path)
        self.max_requests = max_requests
        self._lock = threading.Lock()
        self._local = threading.local()
        self.init_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Return a per-thread SQLite connection (creates if absent)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._get_connection()
        with self._lock:
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Create tables if they do not already exist."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trace_requests (
                    id          TEXT PRIMARY KEY,
                    trace_id    TEXT NOT NULL,
                    span_id     TEXT NOT NULL,
                    method      TEXT NOT NULL,
                    path        TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    started_at  TEXT NOT NULL,
                    data        TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_trace_requests_started_at
                ON trace_requests(started_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_trace_requests_trace_id
                ON trace_requests(trace_id)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS celery_tasks (
                    id               TEXT PRIMARY KEY,
                    task_id          TEXT NOT NULL,
                    trace_id         TEXT NOT NULL,
                    parent_trace_id  TEXT NOT NULL,
                    task_name        TEXT NOT NULL,
                    state            TEXT NOT NULL,
                    enqueued_at      TEXT NOT NULL,
                    data             TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_celery_tasks_parent_trace_id
                ON celery_tasks(parent_trace_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_celery_tasks_task_id
                ON celery_tasks(task_id)
            """)

    # ------------------------------------------------------------------
    # TraceRequest CRUD
    # ------------------------------------------------------------------

    def save_request(self, req: TraceRequest) -> None:
        """Persist a TraceRequest. Prunes oldest records if over max_requests."""
        data_json = json.dumps(req.to_dict())
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO trace_requests
                    (id, trace_id, span_id, method, path, status_code, duration_ms, started_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                req.id,
                req.trace_id,
                req.span_id,
                req.method,
                req.path,
                req.status_code,
                req.duration_ms,
                req.started_at.isoformat(),
                data_json,
            ))
        self._prune_old_requests()

    def _prune_old_requests(self) -> None:
        """Remove oldest requests if total exceeds max_requests."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM trace_requests")
            count = cur.fetchone()[0]
            if count > self.max_requests:
                excess = count - self.max_requests
                cur.execute("""
                    DELETE FROM trace_requests
                    WHERE id IN (
                        SELECT id FROM trace_requests
                        ORDER BY started_at ASC
                        LIMIT ?
                    )
                """, (excess,))

    def get_request(self, request_id: str) -> Optional[TraceRequest]:
        """Fetch a single TraceRequest by its UUID, with Celery tasks stitched in."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT data FROM trace_requests WHERE id = ?", (request_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        req = TraceRequest.from_dict(json.loads(row["data"]))
        req.celery_tasks = self.get_tasks_for_trace(req.trace_id)
        return req

    def get_request_by_trace_id(self, trace_id: str) -> Optional[TraceRequest]:
        """Fetch a TraceRequest by OTel trace ID."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT data FROM trace_requests WHERE trace_id = ? LIMIT 1",
                (trace_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        req = TraceRequest.from_dict(json.loads(row["data"]))
        req.celery_tasks = self.get_tasks_for_trace(req.trace_id)
        return req

    def list_requests(
        self, limit: int = 50, offset: int = 0
    ) -> List[TraceRequest]:
        """Return a paginated list of TraceRequests, newest first."""
        with self._cursor() as cur:
            cur.execute("""
                SELECT data FROM trace_requests
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            rows = cur.fetchall()
        return [TraceRequest.from_dict(json.loads(r["data"])) for r in rows]

    def count_requests(self) -> int:
        """Return total number of stored requests."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM trace_requests")
            return cur.fetchone()[0]

    def delete_request(self, request_id: str) -> None:
        """Remove a single request and its associated tasks."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT trace_id FROM trace_requests WHERE id = ?", (request_id,)
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "DELETE FROM celery_tasks WHERE parent_trace_id = ?",
                    (row["trace_id"],),
                )
            cur.execute("DELETE FROM trace_requests WHERE id = ?", (request_id,))

    def clear_all(self) -> None:
        """Delete all stored data."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM trace_requests")
            cur.execute("DELETE FROM celery_tasks")

    def add_span_to_request(self, trace_id: str, span_dict: dict) -> None:
        """Append a span to an existing TraceRequest identified by trace_id."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, data FROM trace_requests WHERE trace_id = ? LIMIT 1",
                (trace_id,),
            )
            row = cur.fetchone()
            if row is None:
                return
            data = json.loads(row["data"])
            data.setdefault("spans", []).append(span_dict)
            cur.execute(
                "UPDATE trace_requests SET data = ? WHERE id = ?",
                (json.dumps(data), row["id"]),
            )

    # ------------------------------------------------------------------
    # CeleryTask CRUD
    # ------------------------------------------------------------------

    def save_celery_task(self, task: CeleryTask) -> None:
        """Upsert a CeleryTask record."""
        data_json = json.dumps(task.to_dict())
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO celery_tasks
                    (id, task_id, trace_id, parent_trace_id, task_name, state, enqueued_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.id,
                task.task_id,
                task.trace_id,
                task.parent_trace_id,
                task.task_name,
                task.state,
                task.enqueued_at.isoformat(),
                data_json,
            ))

    def get_task_by_celery_id(self, task_id: str) -> Optional[CeleryTask]:
        """Fetch a CeleryTask by its Celery-assigned task UUID."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT data FROM celery_tasks WHERE task_id = ? LIMIT 1",
                (task_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return CeleryTask.from_dict(json.loads(row["data"]))

    def get_tasks_for_trace(self, trace_id: str) -> List[CeleryTask]:
        """Return all Celery tasks whose parent_trace_id matches the given trace_id."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT data FROM celery_tasks WHERE parent_trace_id = ? ORDER BY enqueued_at ASC",
                (trace_id,),
            )
            rows = cur.fetchall()
        return [CeleryTask.from_dict(json.loads(r["data"])) for r in rows]

    def update_task_state(
        self,
        task_id: str,
        state: str,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        duration_ms: Optional[float] = None,
        result: Optional[str] = None,
        exception: Optional[str] = None,
    ) -> None:
        """Update the state fields of an existing CeleryTask."""
        task = self.get_task_by_celery_id(task_id)
        if task is None:
            return
        task.state = state
        if started_at is not None:
            task.started_at = started_at
        if completed_at is not None:
            task.completed_at = completed_at
        if duration_ms is not None:
            task.duration_ms = duration_ms
        if result is not None:
            task.result = result
        if exception is not None:
            task.exception = exception
        with self._cursor() as cur:
            cur.execute("""
                UPDATE celery_tasks
                SET state = ?, data = ?
                WHERE task_id = ?
            """, (task.state, json.dumps(task.to_dict()), task_id))

    def close(self) -> None:
        """Close the per-thread connection if open."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None


# Module-level default storage instance (lazy-initialized).
_default_storage: Optional[TraceStorage] = None
_storage_lock = threading.Lock()


def get_default_storage(db_path: str = _DEFAULT_DB_PATH) -> TraceStorage:
    """Return (or create) the process-wide default TraceStorage instance."""
    global _default_storage
    if _default_storage is None:
        with _storage_lock:
            if _default_storage is None:
                _default_storage = TraceStorage(db_path=db_path)
    return _default_storage


def set_default_storage(storage: TraceStorage) -> None:
    """Replace the default storage instance (useful in tests)."""
    global _default_storage
    _default_storage = storage
