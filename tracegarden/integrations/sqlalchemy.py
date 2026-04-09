"""
tracegarden.integrations.sqlalchemy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Automatic SQLAlchemy query capture via engine events.

Usage (Flask-SQLAlchemy or plain SQLAlchemy)::

    from tracegarden.integrations.sqlalchemy import install_sqlalchemy_instrumentation
    install_sqlalchemy_instrumentation(db.engine)   # Flask-SQLAlchemy
    install_sqlalchemy_instrumentation(engine)       # plain SQLAlchemy

Or to auto-detect all engines at import time (call once after engine creation)::

    from tracegarden.integrations.sqlalchemy import auto_instrument_sqlalchemy
    auto_instrument_sqlalchemy()
"""
from __future__ import annotations

import time
import logging
from datetime import datetime, timezone
from typing import Any

from tracegarden.core.redaction import Redactor
from tracegarden.core.runtime import get_runtime_redactor

logger = logging.getLogger(__name__)

_INSTRUMENTED_ENGINES: set = set()
_FALLBACK_REDACTOR = Redactor()


def install_sqlalchemy_instrumentation(engine: Any) -> None:
    """
    Attach TraceGarden before/after_cursor_execute listeners to *engine*.

    Safe to call multiple times on the same engine — duplicate instrumentation
    is silently skipped.
    """
    engine_id = id(engine)
    if engine_id in _INSTRUMENTED_ENGINES:
        return

    try:
        from sqlalchemy import event  # type: ignore[import]
    except ImportError:
        logger.debug("SQLAlchemy not installed; skipping auto-instrumentation")
        return

    _state: dict[int, dict] = {}  # keyed by id(cursor)

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        _state[id(cursor)] = {
            "sql": statement,
            "params": parameters,
            "started_at": datetime.now(timezone.utc),
            "t0": time.perf_counter(),
        }

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        entry = _state.pop(id(cursor), None)
        if entry is None:
            return

        from tracegarden.core.context import add_db_query, get_current_trace_context
        ctx = get_current_trace_context()
        if not ctx.get("trace_id"):
            return

        duration_ms = (time.perf_counter() - entry["t0"]) * 1000.0

        try:
            from tracegarden.core.fingerprint import fingerprint_sql
            from tracegarden.core.models import DBQuery

            # Resolve vendor from the dialect name (e.g. "postgresql", "mysql", "sqlite")
            db_vendor = getattr(engine.dialect, "name", "unknown")

            redactor = get_runtime_redactor() or _FALLBACK_REDACTOR
            q = DBQuery.create(
                trace_id=ctx["trace_id"],
                span_id=ctx.get("span_id", ""),
                sql=entry["sql"],
                fingerprint=fingerprint_sql(entry["sql"]),
                duration_ms=duration_ms,
                parameters=redactor.redact_db_params(entry["params"]),
                db_vendor=db_vendor,
                started_at=entry["started_at"],
            )
            add_db_query(q)
        except Exception:
            logger.debug("Failed to record SQLAlchemy query", exc_info=True)

    @event.listens_for(engine, "handle_error")
    def _on_error(exception_context):
        # Clean up dangling state if a query errors out before after_cursor_execute.
        cursor = getattr(exception_context, "cursor", None)
        if cursor is not None:
            _state.pop(id(cursor), None)

    _INSTRUMENTED_ENGINES.add(engine_id)
    logger.debug("TraceGarden: SQLAlchemy instrumentation installed on engine %r", engine)


def auto_instrument_sqlalchemy() -> None:
    """
    Attempt to instrument all known SQLAlchemy engines automatically.

    Covers the most common cases:
    - Flask-SQLAlchemy (``flask_sqlalchemy.SQLAlchemy``)
    - Starlette/FastAPI with ``databases`` + SQLAlchemy core engine

    Call this once after your app and engine are fully initialised.
    """
    # Flask-SQLAlchemy
    try:
        from flask import current_app  # type: ignore[import]
        from flask_sqlalchemy import SQLAlchemy  # type: ignore[import]

        ext = current_app.extensions.get("sqlalchemy")
        if ext is not None:
            engine = ext.db.engine if hasattr(ext, "db") else getattr(ext, "engine", None)
            if engine is not None:
                install_sqlalchemy_instrumentation(engine)
                return
    except Exception:
        pass

    logger.debug(
        "TraceGarden: auto_instrument_sqlalchemy() found no engines to instrument. "
        "Call install_sqlalchemy_instrumentation(engine) directly."
    )
