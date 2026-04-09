"""
tracegarden.integrations.celery.signals
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Celery signal handlers for task lifecycle events and request-task stitching.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from tracegarden.core.context import get_current_trace_id
from tracegarden.core.redaction import Redactor
from tracegarden.core.storage import TraceStorage
from tracegarden.core.tracecontext import new_trace_id

try:
    from celery.signals import (  # type: ignore[import]
        before_task_publish,
        task_unknown,
        task_failure,
        task_postrun,
        task_prerun,
        task_retry,
    )

    _CELERY_AVAILABLE = True
except ImportError:
    _CELERY_AVAILABLE = False

_TRACEGARDEN_PARENT_KEY = "tracegarden_parent_trace_id"
_TRACEGARDEN_TRACE_KEY = "tracegarden_trace_id"
_SIGNALS_CONNECTED = False
_STORAGE: Optional[TraceStorage] = None
_REDACTOR: Optional[Redactor] = None


def _get_storage():
    global _STORAGE
    if _STORAGE is None:
        _STORAGE = TraceStorage()
    return _STORAGE


def _get_redactor() -> Redactor:
    global _REDACTOR
    if _REDACTOR is None:
        _REDACTOR = Redactor()
    return _REDACTOR


def configure_runtime(
    storage: Optional[TraceStorage] = None,
    redactor: Optional[Redactor] = None,
) -> None:
    """Set explicit runtime dependencies used by Celery signal handlers."""
    global _STORAGE, _REDACTOR
    if storage is not None:
        _STORAGE = storage
    if redactor is not None:
        _REDACTOR = redactor


def connect_signals(
    storage: Optional[TraceStorage] = None,
    redactor: Optional[Redactor] = None,
) -> None:
    """Connect all TraceGarden Celery signal handlers."""
    global _SIGNALS_CONNECTED
    if not _CELERY_AVAILABLE:
        raise RuntimeError(
            "Celery is not installed. Install it with: pip install tracegarden[celery]"
        )
    configure_runtime(storage=storage, redactor=redactor)
    if _SIGNALS_CONNECTED:
        return
    before_task_publish.connect(_on_before_task_publish, weak=False)
    task_prerun.connect(_on_task_prerun, weak=False)
    task_postrun.connect(_on_task_postrun, weak=False)
    task_failure.connect(_on_task_failure, weak=False)
    task_retry.connect(_on_task_retry, weak=False)
    task_unknown.connect(_on_task_unknown, weak=False)
    _SIGNALS_CONNECTED = True


def disconnect_signals() -> None:
    """Disconnect all TraceGarden Celery signal handlers."""
    global _SIGNALS_CONNECTED
    if not _CELERY_AVAILABLE:
        return
    if not _SIGNALS_CONNECTED:
        return
    before_task_publish.disconnect(_on_before_task_publish)
    task_prerun.disconnect(_on_task_prerun)
    task_postrun.disconnect(_on_task_postrun)
    task_failure.disconnect(_on_task_failure)
    task_retry.disconnect(_on_task_retry)
    task_unknown.disconnect(_on_task_unknown)
    _SIGNALS_CONNECTED = False


def _on_before_task_publish(
    sender=None,
    headers=None,
    body=None,
    routing_key=None,
    **kwargs,
) -> None:
    """Inject trace headers and persist a queued task event."""
    if headers is None:
        return

    parent_trace_id = headers.get(_TRACEGARDEN_PARENT_KEY) or get_current_trace_id()

    # Skip tasks with no parent trace — they cannot be correlated to any request
    # in the UI and would pollute the orphan-task bucket.
    if not parent_trace_id:
        return

    task_trace_id = headers.get(_TRACEGARDEN_TRACE_KEY) or new_trace_id()
    headers[_TRACEGARDEN_TRACE_KEY] = task_trace_id
    headers[_TRACEGARDEN_PARENT_KEY] = parent_trace_id

    task_id = headers.get("id") or headers.get("task_id")
    if not task_id:
        return

    from tracegarden.core.models import CeleryTask

    args, kwargs_payload = _extract_args_kwargs(body)
    args, kwargs_payload = _redact_task_payload(args, kwargs_payload)
    task_name = headers.get("task") or sender or "unknown"
    queue_name = routing_key or "default"

    celery_task = CeleryTask.create(
        task_id=task_id,
        trace_id=task_trace_id,
        parent_trace_id=parent_trace_id,
        task_name=str(task_name),
        queue=queue_name,
        args=args,
        kwargs=kwargs_payload,
    )
    celery_task.state = "PENDING"
    _get_storage().save_celery_task(celery_task)


def _on_task_prerun(
    task_id: str = None,
    task=None,
    args=None,
    kwargs=None,
    **extra,
) -> None:
    """Set task state to STARTED; create task record if missing."""
    from tracegarden.core.models import CeleryTask

    storage = _get_storage()

    existing = storage.get_task_by_celery_id(task_id)
    if existing:
        storage.update_task_state(
            task_id,
            state="STARTED",
            started_at=datetime.now(timezone.utc),
        )
        return

    req_headers = _task_request_headers(task)
    parent_trace_id = req_headers.get(_TRACEGARDEN_PARENT_KEY, "")

    # Skip tasks with no parent trace — nothing to stitch to.
    if not parent_trace_id:
        return

    trace_id = req_headers.get(_TRACEGARDEN_TRACE_KEY, new_trace_id())

    queue_info = getattr(getattr(task, "request", None), "delivery_info", {})
    queue_name = queue_info.get("routing_key", "default") if isinstance(queue_info, dict) else "default"

    redacted_args, redacted_kwargs = _redact_task_payload(list(args or []), dict(kwargs or {}))

    celery_task = CeleryTask.create(
        task_id=task_id,
        trace_id=trace_id,
        parent_trace_id=parent_trace_id,
        task_name=task.name if task else "unknown",
        queue=queue_name,
        args=redacted_args,
        kwargs=redacted_kwargs,
    )
    celery_task.state = "STARTED"
    celery_task.started_at = datetime.now(timezone.utc)
    storage.save_celery_task(celery_task)


def _on_task_postrun(
    task_id: str = None,
    task=None,
    args=None,
    kwargs=None,
    retval=None,
    state: str = "SUCCESS",
    **extra,
) -> None:
    """Mark completion and duration."""
    storage = _get_storage()
    completed_at = datetime.now(timezone.utc)

    existing = storage.get_task_by_celery_id(task_id)
    started_at = existing.started_at if existing else None
    duration_ms: Optional[float] = None
    if started_at:
        duration_ms = (completed_at - started_at).total_seconds() * 1000.0

    result_str: Optional[str] = None
    if retval is not None:
        try:
            result_str = json.dumps(retval)
        except (TypeError, ValueError):
            result_str = str(retval)

    storage.update_task_state(
        task_id,
        state=state,
        completed_at=completed_at,
        duration_ms=duration_ms,
        result=result_str,
    )


def _on_task_failure(
    task_id: str = None,
    exception=None,
    traceback=None,
    einfo=None,
    **extra,
) -> None:
    """Record failure details."""
    storage = _get_storage()
    completed_at = datetime.now(timezone.utc)

    existing = storage.get_task_by_celery_id(task_id)
    started_at = existing.started_at if existing else None
    duration_ms: Optional[float] = None
    if started_at:
        duration_ms = (completed_at - started_at).total_seconds() * 1000.0

    exc_str = f"{type(exception).__name__}: {exception}" if exception else "Unknown error"

    storage.update_task_state(
        task_id,
        state="FAILURE",
        completed_at=completed_at,
        duration_ms=duration_ms,
        exception=exc_str,
    )


def _on_task_retry(
    request=None,
    reason=None,
    einfo=None,
    **extra,
) -> None:
    """Mark task retry."""
    if request is None:
        return
    task_id = request.id
    if task_id:
        _get_storage().update_task_state(task_id, state="RETRY")


def _on_task_unknown(
    name=None,
    id=None,
    message=None,
    exc=None,
    **extra,
) -> None:
    """
    Record unknown-task rejections as failures so they appear in timeline.

    This captures cases where publish succeeded but the worker does not have
    the task registered (e.g. missing import/include).
    """
    if not id:
        return

    reason = str(exc) if exc else f"Unknown task type: {name or 'unknown'}"
    _get_storage().update_task_state(
        id,
        state="FAILURE",
        completed_at=datetime.now(timezone.utc),
        exception=reason,
    )


def _task_request_headers(task) -> dict:
    req = getattr(task, "request", None)
    if req is None:
        return {}

    headers = getattr(req, "headers", None)
    if isinstance(headers, dict):
        return headers

    if isinstance(req, dict):
        maybe = req.get("headers")
        if isinstance(maybe, dict):
            return maybe

    get_fn = getattr(req, "get", None)
    if callable(get_fn):
        maybe = get_fn("headers")
        if isinstance(maybe, dict):
            return maybe

    return {}


def _extract_args_kwargs(body: Any) -> Tuple[list, dict]:
    if body is None:
        return [], {}

    if isinstance(body, (list, tuple)):
        if len(body) >= 2 and isinstance(body[0], (list, tuple)) and isinstance(body[1], dict):
            return list(body[0]), dict(body[1])
        if len(body) == 1 and isinstance(body[0], (list, tuple)):
            return list(body[0]), {}

    if isinstance(body, dict):
        args = body.get("args", [])
        kw = body.get("kwargs", {})
        return list(args) if isinstance(args, (list, tuple)) else [], dict(kw) if isinstance(kw, dict) else {}

    return [], {}


def _redact_task_payload(args: list, kwargs_payload: dict) -> Tuple[list, dict]:
    redactor = _get_redactor()
    safe_args = redactor.redact_db_params(args)
    safe_kwargs = redactor.redact_db_params(kwargs_payload)
    if not isinstance(safe_args, list):
        safe_args = list(args)
    if not isinstance(safe_kwargs, dict):
        safe_kwargs = dict(kwargs_payload)
    return safe_args, safe_kwargs
