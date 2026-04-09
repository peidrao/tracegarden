"""
tracegarden.core.context
~~~~~~~~~~~~~~~~~~~~~~~
Request-scoped trace and captured-event context shared across integrations.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, List

_trace_id_var: ContextVar[str] = ContextVar("tracegarden_trace_id", default="")
_span_id_var: ContextVar[str] = ContextVar("tracegarden_span_id", default="")
_db_vendor_var: ContextVar[str] = ContextVar("tracegarden_db_vendor", default="unknown")
_db_queries_var: ContextVar[List[Any] | None] = ContextVar("tracegarden_db_queries", default=None)
_http_calls_var: ContextVar[List[Any] | None] = ContextVar("tracegarden_http_calls", default=None)


def set_request_context(trace_id: str, span_id: str, db_vendor: str = "unknown") -> None:
    _trace_id_var.set(trace_id)
    _span_id_var.set(span_id)
    _db_vendor_var.set(db_vendor)


def clear_request_context() -> None:
    _trace_id_var.set("")
    _span_id_var.set("")
    _db_vendor_var.set("unknown")
    _db_queries_var.set([])
    _http_calls_var.set([])


def reset_events() -> None:
    _db_queries_var.set([])
    _http_calls_var.set([])


def get_current_trace_context() -> Dict[str, str]:
    return {
        "trace_id": _trace_id_var.get(),
        "span_id": _span_id_var.get(),
        "db_vendor": _db_vendor_var.get(),
    }


def get_current_trace_id() -> str:
    return _trace_id_var.get()


def get_current_span_id() -> str:
    return _span_id_var.get()


def _ensure_list(var: ContextVar[List[Any] | None]) -> List[Any]:
    current = var.get()
    if current is None:
        current = []
        var.set(current)
    return current


def add_db_query(query: Any) -> None:
    _ensure_list(_db_queries_var).append(query)


def get_db_queries() -> List[Any]:
    return list(_ensure_list(_db_queries_var))


def add_http_call(call: Any) -> None:
    _ensure_list(_http_calls_var).append(call)


def get_http_calls() -> List[Any]:
    return list(_ensure_list(_http_calls_var))
