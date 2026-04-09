"""
tracegarden.core.models
~~~~~~~~~~~~~~~~~~~~~~~
Dataclass models for all TraceGarden event types.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class DBQuery:
    """A single database query execution captured during a request."""

    id: str
    trace_id: str
    span_id: str
    sql: str
    fingerprint: str
    duration_ms: float
    started_at: datetime
    parameters: list
    db_vendor: str  # "sqlite" | "postgres" | "mysql"
    is_duplicate: bool = False
    duplicate_count: int = 1

    @classmethod
    def create(
        cls,
        trace_id: str,
        span_id: str,
        sql: str,
        fingerprint: str,
        duration_ms: float,
        parameters: list,
        db_vendor: str = "sqlite",
        started_at: Optional[datetime] = None,
    ) -> "DBQuery":
        return cls(
            id=_new_id(),
            trace_id=trace_id,
            span_id=span_id,
            sql=sql,
            fingerprint=fingerprint,
            duration_ms=duration_ms,
            started_at=started_at or _now(),
            parameters=parameters,
            db_vendor=db_vendor,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "sql": self.sql,
            "fingerprint": self.fingerprint,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat(),
            "parameters": self.parameters,
            "db_vendor": self.db_vendor,
            "is_duplicate": self.is_duplicate,
            "duplicate_count": self.duplicate_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DBQuery":
        return cls(
            id=data["id"],
            trace_id=data["trace_id"],
            span_id=data["span_id"],
            sql=data["sql"],
            fingerprint=data["fingerprint"],
            duration_ms=data["duration_ms"],
            started_at=datetime.fromisoformat(data["started_at"]),
            parameters=data["parameters"],
            db_vendor=data["db_vendor"],
            is_duplicate=data.get("is_duplicate", False),
            duplicate_count=data.get("duplicate_count", 1),
        )


@dataclass
class HTTPCall:
    """An outgoing HTTP request made during request processing."""

    id: str
    trace_id: str
    method: str
    url: str
    status_code: int
    duration_ms: float
    started_at: datetime
    request_headers: dict
    response_headers: dict

    @classmethod
    def create(
        cls,
        trace_id: str,
        method: str,
        url: str,
        status_code: int,
        duration_ms: float,
        request_headers: Optional[dict] = None,
        response_headers: Optional[dict] = None,
        started_at: Optional[datetime] = None,
    ) -> "HTTPCall":
        return cls(
            id=_new_id(),
            trace_id=trace_id,
            method=method,
            url=url,
            status_code=status_code,
            duration_ms=duration_ms,
            started_at=started_at or _now(),
            request_headers=request_headers or {},
            response_headers=response_headers or {},
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat(),
            "request_headers": self.request_headers,
            "response_headers": self.response_headers,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HTTPCall":
        return cls(
            id=data["id"],
            trace_id=data["trace_id"],
            method=data["method"],
            url=data["url"],
            status_code=data["status_code"],
            duration_ms=data["duration_ms"],
            started_at=datetime.fromisoformat(data["started_at"]),
            request_headers=data.get("request_headers", {}),
            response_headers=data.get("response_headers", {}),
        )


@dataclass
class Span:
    """An OpenTelemetry span associated with a request."""

    id: str
    trace_id: str
    parent_span_id: Optional[str]
    name: str
    kind: str  # "SERVER" | "CLIENT" | "INTERNAL" | "PRODUCER" | "CONSUMER"
    started_at: datetime
    duration_ms: float
    attributes: dict
    status: str  # "OK" | "ERROR" | "UNSET"

    @classmethod
    def create(
        cls,
        trace_id: str,
        name: str,
        kind: str = "INTERNAL",
        parent_span_id: Optional[str] = None,
        duration_ms: float = 0.0,
        attributes: Optional[dict] = None,
        status: str = "UNSET",
        started_at: Optional[datetime] = None,
        span_id: Optional[str] = None,
    ) -> "Span":
        return cls(
            id=span_id or _new_id(),
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            started_at=started_at or _now(),
            duration_ms=duration_ms,
            attributes=attributes or {},
            status=status,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Span":
        return cls(
            id=data["id"],
            trace_id=data["trace_id"],
            parent_span_id=data.get("parent_span_id"),
            name=data["name"],
            kind=data["kind"],
            started_at=datetime.fromisoformat(data["started_at"]),
            duration_ms=data["duration_ms"],
            attributes=data.get("attributes", {}),
            status=data.get("status", "UNSET"),
        )


@dataclass
class CeleryTask:
    """A background Celery task linked to a web request via trace ID."""

    id: str
    task_id: str
    trace_id: str           # OTel trace ID propagated through task headers
    parent_trace_id: str    # Web request trace ID — the stitching key
    task_name: str
    state: str              # "PENDING" | "STARTED" | "SUCCESS" | "FAILURE" | "RETRY" | "REVOKED"
    queue: str
    enqueued_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_ms: Optional[float]
    args: list
    kwargs: dict
    result: Optional[str]
    exception: Optional[str]

    @classmethod
    def create(
        cls,
        task_id: str,
        trace_id: str,
        parent_trace_id: str,
        task_name: str,
        queue: str = "default",
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
        enqueued_at: Optional[datetime] = None,
    ) -> "CeleryTask":
        return cls(
            id=_new_id(),
            task_id=task_id,
            trace_id=trace_id,
            parent_trace_id=parent_trace_id,
            task_name=task_name,
            state="PENDING",
            queue=queue,
            enqueued_at=enqueued_at or _now(),
            started_at=None,
            completed_at=None,
            duration_ms=None,
            args=args or [],
            kwargs=kwargs or {},
            result=None,
            exception=None,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "parent_trace_id": self.parent_trace_id,
            "task_name": self.task_name,
            "state": self.state,
            "queue": self.queue,
            "enqueued_at": self.enqueued_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "args": self.args,
            "kwargs": self.kwargs,
            "result": self.result,
            "exception": self.exception,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CeleryTask":
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            trace_id=data["trace_id"],
            parent_trace_id=data["parent_trace_id"],
            task_name=data["task_name"],
            state=data["state"],
            queue=data["queue"],
            enqueued_at=datetime.fromisoformat(data["enqueued_at"]),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            duration_ms=data.get("duration_ms"),
            args=data.get("args", []),
            kwargs=data.get("kwargs", {}),
            result=data.get("result"),
            exception=data.get("exception"),
        )


@dataclass
class TraceRequest:
    """Root record for a single HTTP request/response cycle."""

    id: str
    trace_id: str
    span_id: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    started_at: datetime
    request_headers: dict
    response_headers: dict
    db_queries: List[DBQuery] = field(default_factory=list)
    http_calls: List[HTTPCall] = field(default_factory=list)
    spans: List[Span] = field(default_factory=list)
    celery_tasks: List[CeleryTask] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        method: str,
        path: str,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        request_headers: Optional[dict] = None,
        started_at: Optional[datetime] = None,
    ) -> "TraceRequest":
        return cls(
            id=_new_id(),
            trace_id=trace_id or _new_id().replace("-", ""),
            span_id=span_id or _new_id().replace("-", "")[:16],
            method=method.upper(),
            path=path,
            status_code=0,
            duration_ms=0.0,
            started_at=started_at or _now(),
            request_headers=request_headers or {},
            response_headers={},
        )

    @property
    def db_query_count(self) -> int:
        return len(self.db_queries)

    @property
    def http_call_count(self) -> int:
        return len(self.http_calls)

    @property
    def task_count(self) -> int:
        return len(self.celery_tasks)

    @property
    def has_n_plus_one(self) -> bool:
        return any(q.is_duplicate and q.duplicate_count >= 5 for q in self.db_queries)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat(),
            "request_headers": self.request_headers,
            "response_headers": self.response_headers,
            "db_queries": [q.to_dict() for q in self.db_queries],
            "http_calls": [h.to_dict() for h in self.http_calls],
            "spans": [s.to_dict() for s in self.spans],
            "celery_tasks": [t.to_dict() for t in self.celery_tasks],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TraceRequest":
        return cls(
            id=data["id"],
            trace_id=data["trace_id"],
            span_id=data["span_id"],
            method=data["method"],
            path=data["path"],
            status_code=data["status_code"],
            duration_ms=data["duration_ms"],
            started_at=datetime.fromisoformat(data["started_at"]),
            request_headers=data.get("request_headers", {}),
            response_headers=data.get("response_headers", {}),
            db_queries=[DBQuery.from_dict(q) for q in data.get("db_queries", [])],
            http_calls=[HTTPCall.from_dict(h) for h in data.get("http_calls", [])],
            spans=[Span.from_dict(s) for s in data.get("spans", [])],
            celery_tasks=[CeleryTask.from_dict(t) for t in data.get("celery_tasks", [])],
            metadata=data.get("metadata", {}),
        )
