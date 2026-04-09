"""
tracegarden.core.storage
~~~~~~~~~~~~~~~~~~~~~~~~
SQLite storage backend for TraceGarden.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import warnings
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .models import CeleryTask, TraceRequest


def _default_db_path() -> str:
    """Return the default SQLite path, creating the parent directory if needed."""
    p = Path.home() / ".tracegarden" / "tracegarden.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


class TraceStorage:
    """Thread-safe SQLite storage for TraceRequest and CeleryTask records."""

    def __init__(self, db_path: str = "", max_requests: int = 5000):
        if not db_path:
            db_path = _default_db_path()
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
            # check_same_thread=True is the default and is correct here because
            # each thread gets its own connection via threading.local.
            conn = sqlite3.connect(self.db_path)
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_spans (
                    trace_id   TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    span_data  TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_pending_spans_trace_id
                ON pending_spans(trace_id)
            """)

    # ------------------------------------------------------------------
    # TraceRequest CRUD
    # ------------------------------------------------------------------

    def save_request(self, req: TraceRequest) -> None:
        """Persist a TraceRequest. Prunes oldest records atomically if over max_requests."""
        with self._cursor() as cur:
            data_dict = req.to_dict()
            cur.execute(
                "SELECT span_data FROM pending_spans WHERE trace_id = ? ORDER BY started_at ASC",
                (req.trace_id,),
            )
            pending_rows = cur.fetchall()
            if pending_rows:
                data_dict.setdefault("spans", []).extend(
                    [json.loads(row["span_data"]) for row in pending_rows]
                )

            data_json = json.dumps(data_dict)
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

            if pending_rows:
                cur.execute("""
                    DELETE FROM pending_spans
                    WHERE trace_id = ?
                """, (req.trace_id,))

            if self.max_requests >= 0:
                cur.execute("""
                    DELETE FROM trace_requests
                    WHERE id IN (
                        SELECT id FROM trace_requests
                        ORDER BY started_at DESC
                        LIMIT -1 OFFSET ?
                    )
                """, (self.max_requests,))

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
            cur.execute("DELETE FROM pending_spans")

    def add_span_to_request(self, trace_id: str, span_dict: dict) -> None:
        """Append a span to an existing TraceRequest identified by trace_id."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, data FROM trace_requests WHERE trace_id = ? LIMIT 1",
                (trace_id,),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO pending_spans (trace_id, started_at, span_data) VALUES (?, ?, ?)",
                    (
                        trace_id,
                        str(span_dict.get("started_at", "")),
                        json.dumps(span_dict),
                    ),
                )
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
        """Update the state fields of an existing CeleryTask atomically."""
        # Fetch, modify, and write within a single lock acquisition to avoid TOCTOU.
        with self._cursor() as cur:
            cur.execute(
                "SELECT data FROM celery_tasks WHERE task_id = ? LIMIT 1",
                (task_id,),
            )
            row = cur.fetchone()
            if row is None:
                return
            task = CeleryTask.from_dict(json.loads(row["data"]))
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


def get_default_storage(db_path: str = "") -> TraceStorage:
    """
    Backward-compatible helper.

    Prefer passing a TraceStorage instance explicitly.
    """
    warnings.warn(
        "get_default_storage() is deprecated; pass TraceStorage explicitly.",
        DeprecationWarning,
        stacklevel=2,
    )
    return TraceStorage(db_path=db_path)


def set_default_storage(storage: TraceStorage) -> None:
    """Backward-compatible no-op."""
    warnings.warn(
        "set_default_storage() is deprecated and is now a no-op.",
        DeprecationWarning,
        stacklevel=2,
    )
