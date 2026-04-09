"""
tracegarden.integrations.flask.extension
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Flask extension for TraceGarden using before/after_request hooks.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from tracegarden.core.context import (
    add_db_query,
    add_http_call,
    clear_request_context,
    get_db_queries,
    get_http_calls,
    reset_events,
    set_request_context,
)
from tracegarden.core.tracecontext import parse_traceparent

if TYPE_CHECKING:
    from tracegarden import TraceGardenConfig
    from tracegarden.core.redaction import Redactor
    from tracegarden.core.storage import TraceStorage


def init_tracegarden_flask(app, config, storage, redactor) -> None:
    """
    Register TraceGarden before/after request hooks on a Flask app and
    mount the UI blueprint.
    """
    from tracegarden.ui.routes import mount_flask_blueprint

    _attach_hooks(app, config, storage, redactor)
    mount_flask_blueprint(app, config=config, storage=storage)


def _attach_hooks(app, config, storage, redactor) -> None:
    """Install before/after_request hooks on the Flask app."""
    @app.before_request
    def _tracegarden_before():
        from flask import request, g  # type: ignore[import]

        if request.path.startswith(config.ui_prefix):
            return

        incoming = parse_traceparent(request.headers.get("traceparent"))
        trace_id = incoming[0] if incoming else str(uuid.uuid4()).replace("-", "")
        span_id = str(uuid.uuid4()).replace("-", "")[:16]
        started_at = datetime.now(timezone.utc)
        set_request_context(trace_id=trace_id, span_id=span_id, db_vendor="sqlite")
        reset_events()

        req_headers = {}
        for k, v in request.headers:
            req_headers[k.lower()] = v
        safe_req_headers = redactor.redact_headers(req_headers)

        g._tg_trace_id = trace_id
        g._tg_span_id = span_id
        g._tg_started_at = started_at
        g._tg_safe_req_headers = safe_req_headers
        g._tg_t0 = time.perf_counter()
        g._tg_db_queries = []
        g._tg_http_calls = []

    @app.after_request
    def _tracegarden_after(response):
        from flask import request, g  # type: ignore[import]
        from tracegarden.core.models import TraceRequest
        from tracegarden.core.fingerprint import annotate_duplicates

        if request.path.startswith(config.ui_prefix):
            return response

        trace_id = getattr(g, "_tg_trace_id", None)
        if trace_id is None:
            return response

        duration_ms = (time.perf_counter() - g._tg_t0) * 1000.0

        resp_headers = {}
        for k, v in response.headers:
            resp_headers[k.lower()] = v
        safe_resp_headers = redactor.redact_headers(resp_headers)

        queries = getattr(g, "_tg_db_queries", [])
        queries = get_db_queries() or queries
        annotate_duplicates(queries)
        http_calls = get_http_calls() or getattr(g, "_tg_http_calls", [])

        trace_req = TraceRequest(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            span_id=g._tg_span_id,
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            started_at=g._tg_started_at,
            request_headers=g._tg_safe_req_headers,
            response_headers=safe_resp_headers,
            db_queries=queries,
            http_calls=http_calls,
            spans=[],
            celery_tasks=[],
            metadata={
                "user_agent": request.user_agent.string if request.user_agent else "",
                "remote_addr": request.remote_addr or "",
                "traceparent": request.headers.get("traceparent", ""),
                "query_string": redactor.redact_url_params(
                    "?" + (request.query_string.decode("utf-8", errors="replace") or "")
                ).lstrip("?"),
            },
        )

        storage.save_request(trace_req)
        clear_request_context()
        return response


def capture_flask_db_query(
    sql: str,
    params: list,
    duration_ms: float,
    db_vendor: str = "sqlite",
    started_at: datetime = None,
) -> None:
    """
    Helper to manually record a DB query from within a Flask request context.
    Call this from your DB layer if not using SQLAlchemy auto-instrumentation.
    """
    try:
        from flask import g, has_request_context  # type: ignore[import]
        from tracegarden.core.fingerprint import fingerprint_sql
        from tracegarden.core.models import DBQuery

        if not has_request_context():
            return

        trace_id = getattr(g, "_tg_trace_id", "")
        span_id = getattr(g, "_tg_span_id", "")
        if not trace_id:
            return

        fp = fingerprint_sql(sql)
        q = DBQuery.create(
            trace_id=trace_id,
            span_id=span_id,
            sql=sql,
            fingerprint=fp,
            duration_ms=duration_ms,
            parameters=params,
            db_vendor=db_vendor,
            started_at=started_at or datetime.now(timezone.utc),
        )
        if not hasattr(g, "_tg_db_queries"):
            g._tg_db_queries = []
        g._tg_db_queries.append(q)
        add_db_query(q)
    except Exception:
        pass


def capture_flask_http_call(
    method: str,
    url: str,
    status_code: int,
    duration_ms: float,
    request_headers: dict = None,
    response_headers: dict = None,
) -> None:
    """Helper to manually record an outgoing HTTP call from a Flask request context."""
    try:
        from flask import g, has_request_context  # type: ignore[import]
        from tracegarden.core.models import HTTPCall
        from tracegarden.core.redaction import get_default_redactor

        if not has_request_context():
            return

        trace_id = getattr(g, "_tg_trace_id", "")
        if not trace_id:
            return

        redactor = get_default_redactor()
        call = HTTPCall.create(
            trace_id=trace_id,
            method=method,
            url=redactor.redact_url_params(url),
            status_code=status_code,
            duration_ms=duration_ms,
            request_headers=redactor.redact_headers(request_headers or {}),
            response_headers=redactor.redact_headers(response_headers or {}),
        )
        if not hasattr(g, "_tg_http_calls"):
            g._tg_http_calls = []
        g._tg_http_calls.append(call)
        add_http_call(call)
    except Exception:
        pass
