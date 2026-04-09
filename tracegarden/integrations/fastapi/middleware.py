"""
tracegarden.integrations.fastapi.middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ASGI middleware for FastAPI / Starlette.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware  # type: ignore[import]
from starlette.requests import Request  # type: ignore[import]
from starlette.responses import Response  # type: ignore[import]
from starlette.types import ASGIApp  # type: ignore[import]

from tracegarden.core.context import (
    add_db_query,
    add_http_call,
    clear_request_context,
    get_current_span_id as _ctx_get_span_id,
    get_current_trace_id as _ctx_get_trace_id,
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


class TraceGardenMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that captures request/response data for TraceGarden."""

    def __init__(
        self,
        app: ASGIApp,
        config: Optional["TraceGardenConfig"] = None,
        storage: Optional["TraceStorage"] = None,
        redactor: Optional["Redactor"] = None,
    ):
        super().__init__(app)
        self._config = config
        self._storage = storage
        self._redactor = redactor

    def _get_config(self):
        if self._config is not None:
            return self._config
        from tracegarden import TraceGardenConfig
        return TraceGardenConfig()

    def _get_storage(self):
        if self._storage is not None:
            return self._storage
        from tracegarden.core.storage import get_default_storage
        return get_default_storage()

    def _get_redactor(self):
        if self._redactor is not None:
            return self._redactor
        from tracegarden.core.redaction import get_default_redactor
        return get_default_redactor()

    async def dispatch(self, request: Request, call_next) -> Response:
        config = self._get_config()
        if not config.enabled or request.url.path.startswith(config.ui_prefix):
            return await call_next(request)

        storage = self._get_storage()
        redactor = self._get_redactor()

        incoming = parse_traceparent(request.headers.get("traceparent"))
        trace_id = incoming[0] if incoming else new_trace_id()
        span_id = new_span_id()
        started_at = datetime.now(timezone.utc)

        set_request_context(trace_id=trace_id, span_id=span_id, db_vendor="unknown")
        reset_events()

        req_headers = {k.decode(): v.decode() for k, v in request.headers.raw}
        safe_req_headers = redactor.redact_headers(req_headers)

        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            clear_request_context()
            raise
        duration_ms = (time.perf_counter() - t0) * 1000.0

        resp_headers = dict(response.headers)
        safe_resp_headers = redactor.redact_headers(resp_headers)

        from tracegarden.core.fingerprint import annotate_duplicates
        from tracegarden.core.models import TraceRequest

        queries = get_db_queries()
        annotate_duplicates(queries)
        http_calls = get_http_calls()

        qs = request.url.query or ""
        trace_req = TraceRequest(
            id=new_trace_id(),
            trace_id=trace_id,
            span_id=span_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            started_at=started_at,
            request_headers=safe_req_headers,
            response_headers=safe_resp_headers,
            db_queries=queries,
            http_calls=http_calls,
            spans=[],
            celery_tasks=[],
            metadata={
                "user_agent": req_headers.get("user-agent", ""),
                "remote_addr": request.client.host if request.client else "",
                "traceparent": request.headers.get("traceparent", ""),
                "query_string": redactor.redact_url_params(
                    "?" + qs if qs else ""
                ).lstrip("?"),
            },
        )

        storage.save_request(trace_req)
        clear_request_context()
        return response


def get_current_trace_id() -> str:
    return _ctx_get_trace_id()


def get_current_span_id() -> str:
    return _ctx_get_span_id()


async def capture_fastapi_db_query(
    sql: str,
    params: list,
    duration_ms: float,
    db_vendor: str = "sqlite",
    started_at: Optional[datetime] = None,
) -> None:
    """Record a DB query in the current ASGI request context."""
    from tracegarden.core.fingerprint import fingerprint_sql
    from tracegarden.core.models import DBQuery

    trace_id = _ctx_get_trace_id()
    if not trace_id:
        return

    q = DBQuery.create(
        trace_id=trace_id,
        span_id=_ctx_get_span_id(),
        sql=sql,
        fingerprint=fingerprint_sql(sql),
        duration_ms=duration_ms,
        parameters=params,
        db_vendor=db_vendor,
        started_at=started_at or datetime.now(timezone.utc),
    )
    add_db_query(q)


async def capture_fastapi_http_call(
    method: str,
    url: str,
    status_code: int,
    duration_ms: float,
    request_headers: Optional[dict] = None,
    response_headers: Optional[dict] = None,
) -> None:
    """Record an outgoing HTTP call in the current ASGI request context."""
    from tracegarden.core.models import HTTPCall
    from tracegarden.core.redaction import get_default_redactor

    trace_id = _ctx_get_trace_id()
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
