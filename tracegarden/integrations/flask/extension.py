"""
tracegarden.integrations.flask.extension
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Flask extension for TraceGarden using before/after_request hooks.
"""
from __future__ import annotations

import logging
import time
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
from tracegarden.core.tracecontext import new_span_id, new_trace_id, parse_traceparent

if TYPE_CHECKING:
    from tracegarden import TraceGardenConfig
    from tracegarden.core.redaction import Redactor
    from tracegarden.core.storage import TraceStorage

logger = logging.getLogger(__name__)


def init_tracegarden_flask(app, config, storage, redactor) -> None:
    """
    Register TraceGarden before/after request hooks on a Flask app and
    mount the UI blueprint.
    """
    from tracegarden.ui.routes import mount_flask_blueprint

    _attach_hooks(app, config, storage, redactor)
    mount_flask_blueprint(app, config=config, storage=storage)
    _try_install_sqlalchemy(app)


def _try_install_sqlalchemy(app) -> None:
    """Auto-instrument SQLAlchemy if Flask-SQLAlchemy is present on the app."""
    try:
        from tracegarden.integrations.sqlalchemy import install_sqlalchemy_instrumentation

        # Flask-SQLAlchemy ≥ 3.x stores the extension under app.extensions["sqlalchemy"]
        ext = app.extensions.get("sqlalchemy") if hasattr(app, "extensions") else None
        if ext is None:
            return

        engine = None
        try:
            with app.app_context():
                if hasattr(ext, "engine"):
                    engine = ext.engine
                elif hasattr(ext, "db") and hasattr(ext.db, "engine"):
                    engine = ext.db.engine
        except Exception:
            logger.debug("TraceGarden: could not resolve SQLAlchemy engine inside app context", exc_info=True)
            return

        if engine is not None:
            install_sqlalchemy_instrumentation(engine)
        else:
            logger.debug(
                "TraceGarden: Flask-SQLAlchemy extension found but could not resolve engine. "
                "Call install_sqlalchemy_instrumentation(engine) manually."
            )
    except Exception:
        logger.debug("TraceGarden: SQLAlchemy auto-instrumentation failed", exc_info=True)


def _detect_flask_db_vendor(app) -> str:
    """Best-effort detection of the DB vendor from Flask-SQLAlchemy."""
    try:
        ext = app.extensions.get("sqlalchemy") if hasattr(app, "extensions") else None
        if ext is None:
            return "unknown"
        engine = None
        with app.app_context():
            if hasattr(ext, "engine"):
                engine = ext.engine
            elif hasattr(ext, "db") and hasattr(ext.db, "engine"):
                engine = ext.db.engine
        if engine is not None:
            return getattr(engine.dialect, "name", "unknown")
    except Exception:
        pass
    return "unknown"


def _attach_hooks(app, config: "TraceGardenConfig", storage: "TraceStorage", redactor: "Redactor") -> None:
    """Install before/after_request hooks on the Flask app."""

    db_vendor = _detect_flask_db_vendor(app)

    @app.before_request
    def _tracegarden_before():
        from flask import g, request  # type: ignore[import]

        if request.path.startswith(config.ui_prefix):
            g._tg_skip = True
            return

        g._tg_skip = False
        incoming = parse_traceparent(request.headers.get("traceparent"))
        trace_id = incoming[0] if incoming else new_trace_id()
        span_id = incoming[1] if incoming else new_span_id()
        started_at = datetime.now(timezone.utc)
        set_request_context(trace_id=trace_id, span_id=span_id, db_vendor=db_vendor)
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

    @app.after_request
    def _tracegarden_after(response):
        from flask import g, request  # type: ignore[import]
        from tracegarden.core.fingerprint import annotate_duplicates
        from tracegarden.core.models import TraceRequest

        if getattr(g, "_tg_skip", True):
            return response

        trace_id = getattr(g, "_tg_trace_id", None)
        if trace_id is None:
            return response

        duration_ms = (time.perf_counter() - g._tg_t0) * 1000.0

        resp_headers = {}
        for k, v in response.headers:
            resp_headers[k.lower()] = v
        safe_resp_headers = redactor.redact_headers(resp_headers)

        # Use context vars as the single source of truth (populated by all integrations)
        queries = get_db_queries()
        annotate_duplicates(queries)
        http_calls = get_http_calls()

        metadata: dict = {
            "user_agent": request.user_agent.string if request.user_agent else "",
            "remote_addr": request.remote_addr or "",
            "traceparent": request.headers.get("traceparent", ""),
            "n_plus_one_threshold": config.n_plus_one_threshold,
            "query_string": redactor.redact_url_params(
                "?" + (request.query_string.decode("utf-8", errors="replace") or "")
            ).lstrip("?"),
        }

        if config.capture_request_body and request.method in ("POST", "PUT", "PATCH"):
            try:
                raw = request.get_data(as_text=True)
                ct = request.content_type or ""
                metadata["request_body"] = redactor.redact_body(raw, ct)
            except Exception:
                logger.debug("Failed to capture request body", exc_info=True)

        if config.capture_response_body:
            try:
                raw = response.get_data(as_text=True)
                ct = response.content_type or ""
                metadata["response_body"] = redactor.redact_body(raw, ct)
            except Exception:
                logger.debug("Failed to capture response body", exc_info=True)

        trace_req = TraceRequest(
            id=new_trace_id(),
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
            metadata=metadata,
        )

        try:
            storage.save_request(trace_req)
        except Exception:
            logger.debug("Failed to save TraceRequest", exc_info=True)

        clear_request_context()
        return response


def capture_flask_db_query(
    sql: str,
    params: object,
    duration_ms: float,
    db_vendor: str = "unknown",
    started_at: datetime = None,  # type: ignore[assignment]
) -> None:
    """
    Helper to manually record a DB query from within a Flask request context.
    Call this from your DB layer if not using SQLAlchemy auto-instrumentation.
    """
    try:
        from tracegarden.core.context import get_current_trace_context
        from tracegarden.core.fingerprint import fingerprint_sql
        from tracegarden.core.models import DBQuery
        from tracegarden.core.redaction import get_default_redactor

        ctx = get_current_trace_context()
        trace_id = ctx.get("trace_id", "")
        span_id = ctx.get("span_id", "")
        if not trace_id:
            return

        redactor = get_default_redactor()
        fp = fingerprint_sql(sql)
        q = DBQuery.create(
            trace_id=trace_id,
            span_id=span_id,
            sql=sql,
            fingerprint=fp,
            duration_ms=duration_ms,
            parameters=redactor.redact_db_params(params),
            db_vendor=db_vendor or ctx.get("db_vendor", "unknown"),
            started_at=started_at or datetime.now(timezone.utc),
        )
        add_db_query(q)
    except Exception:
        logger.debug("Failed to capture Flask DB query", exc_info=True)


def capture_flask_http_call(
    method: str,
    url: str,
    status_code: int,
    duration_ms: float,
    request_headers: dict = None,  # type: ignore[assignment]
    response_headers: dict = None,  # type: ignore[assignment]
) -> None:
    """Helper to manually record an outgoing HTTP call from a Flask request context."""
    try:
        from tracegarden.core.context import get_current_trace_context
        from tracegarden.core.models import HTTPCall
        from tracegarden.core.redaction import get_default_redactor

        trace_id = get_current_trace_context().get("trace_id", "")
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
        add_http_call(call)
    except Exception:
        logger.debug("Failed to capture Flask HTTP call", exc_info=True)
