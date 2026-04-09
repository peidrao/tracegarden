"""
tracegarden.integrations.django.signals
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Django database execute wrapper for SQL query capture.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from tracegarden.core.context import (
    add_db_query,
    get_current_trace_context,
    reset_events,
    set_request_context,
    clear_request_context,
    get_db_queries,
)
from tracegarden.core.runtime import get_runtime_redactor


def get_pending_queries() -> list:
    """Backward-compatible alias used by Django middleware."""
    return get_db_queries()


def add_pending_query(query) -> None:
    add_db_query(query)


def clear_pending_queries() -> None:
    reset_events()


def _record_query(execute, sql: str, params, many: bool, context):
    """
    Django database execute wrapper.

    Django calls wrappers as ``wrapper(execute, sql, params, many, context)``
    where ``context`` is ``{'connection': ..., 'cursor': ...}``.
    Installed per-request by ``TraceGardenMiddleware``.
    """
    from tracegarden.core.fingerprint import fingerprint_sql
    from tracegarden.core.models import DBQuery

    ctx = get_current_trace_context()
    if not ctx.get("trace_id"):
        return execute(sql, params, many, context)

    # Resolve vendor from the actual connection so multi-db apps are handled.
    db_vendor = ctx.get("db_vendor", "unknown")
    try:
        db_vendor = context["connection"].vendor
    except Exception:
        pass

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    try:
        return execute(sql, params, many, context)
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        redactor = get_runtime_redactor()
        if redactor is None:
            return
        safe_params = redactor.redact_db_params(params)
        q = DBQuery.create(
            trace_id=ctx.get("trace_id", ""),
            span_id=ctx.get("span_id", ""),
            sql=sql,
            fingerprint=fingerprint_sql(sql),
            duration_ms=duration_ms,
            parameters=safe_params,
            db_vendor=db_vendor,
            started_at=started,
        )
        add_db_query(q)


# Backward-compatible aliases used by other modules.
set_current_trace_context = set_request_context
clear_current_trace_context = clear_request_context
