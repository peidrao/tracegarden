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


def get_pending_queries() -> list:
    """Backward-compatible alias used by Django middleware."""
    return get_db_queries()


def add_pending_query(query) -> None:
    add_db_query(query)


def clear_pending_queries() -> None:
    reset_events()


def _record_query(
    sql: str,
    params,
    many: bool,
    execute,
    *args,
    **kwargs,
):
    """
    Django database execute wrapper.
    Installed per-request by ``TraceGardenMiddleware``.
    """
    from tracegarden.core.fingerprint import fingerprint_sql
    from tracegarden.core.models import DBQuery
    from tracegarden.core.redaction import get_default_redactor

    ctx = get_current_trace_context()
    if not ctx.get("trace_id"):
        return execute(sql, params, many, *args, **kwargs)

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    try:
        return execute(sql, params, many, *args, **kwargs)
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        redactor = get_default_redactor()
        safe_params = redactor.redact_db_params(params)
        q = DBQuery.create(
            trace_id=ctx.get("trace_id", ""),
            span_id=ctx.get("span_id", ""),
            sql=sql,
            fingerprint=fingerprint_sql(sql),
            duration_ms=duration_ms,
            parameters=safe_params,
            db_vendor=ctx.get("db_vendor", "unknown"),
            started_at=started,
        )
        add_db_query(q)


# Backward-compatible aliases used by other modules.
set_current_trace_context = set_request_context
clear_current_trace_context = clear_request_context
