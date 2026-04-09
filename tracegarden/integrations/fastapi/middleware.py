"""
tracegarden.integrations.fastapi.middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ASGI middleware for FastAPI / Starlette.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from starlette.types import ASGIApp, Message, Receive, Scope, Send  # type: ignore[import]

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
from tracegarden.core.redaction import Redactor
from tracegarden.core.runtime import bind_runtime, get_runtime_redactor, reset_runtime
from tracegarden.core.storage import TraceStorage
from tracegarden.core.tracecontext import new_span_id, new_trace_id, parse_traceparent

if TYPE_CHECKING:
    from tracegarden import TraceGardenConfig
    from tracegarden.core.redaction import Redactor
    from tracegarden.core.storage import TraceStorage

logger = logging.getLogger(__name__)


class TraceGardenMiddleware:
    """ASGI middleware that captures request/response data for TraceGarden."""

    def __init__(
        self,
        app: ASGIApp,
        config: Optional["TraceGardenConfig"] = None,
        storage: Optional["TraceStorage"] = None,
        redactor: Optional["Redactor"] = None,
        sqlalchemy_engine: Optional[object] = None,
    ):
        self.app = app
        self._config = config
        self._storage = storage
        self._redactor = redactor
        if sqlalchemy_engine is not None:
            try:
                from tracegarden.integrations.sqlalchemy import install_sqlalchemy_instrumentation

                install_sqlalchemy_instrumentation(sqlalchemy_engine)
            except Exception:
                logger.debug("TraceGarden: SQLAlchemy auto-instrumentation failed", exc_info=True)

    def _get_config(self) -> "TraceGardenConfig":
        if self._config is not None:
            return self._config
        from tracegarden import TraceGardenConfig

        return TraceGardenConfig()

    def _get_storage(self) -> "TraceStorage":
        if self._storage is not None:
            return self._storage
        cfg = self._get_config()
        self._storage = TraceStorage(
            db_path=cfg.db_path,
            max_requests=cfg.max_requests,
        )
        return self._storage

    def _get_redactor(self) -> "Redactor":
        if self._redactor is not None:
            return self._redactor
        cfg = self._get_config()
        self._redactor = Redactor(
            header_denylist=set(cfg.redact_headers),
            param_denylist=set(cfg.redact_params),
            header_allowlist=set(cfg.header_allowlist),
        )
        return self._redactor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        config = self._get_config()
        path = str(scope.get("path", ""))
        if not config.enabled or path.startswith(config.ui_prefix):
            await self.app(scope, receive, send)
            return

        storage = self._get_storage()
        redactor = self._get_redactor()
        runtime_tokens = bind_runtime(storage, redactor)

        raw_headers = scope.get("headers") or []
        req_headers = {
            str(k, "latin-1").lower(): str(v, "latin-1")
            for k, v in raw_headers
        }

        incoming = parse_traceparent(req_headers.get("traceparent"))
        trace_id = incoming[0] if incoming else new_trace_id()
        parent_span_id = incoming[1] if incoming else ""
        span_id = new_span_id()
        started_at = datetime.now(timezone.utc)

        set_request_context(trace_id=trace_id, span_id=span_id, db_vendor="unknown")
        reset_events()

        metadata: dict = {
            "user_agent": req_headers.get("user-agent", ""),
            "remote_addr": (scope.get("client") or ("", 0))[0] if scope.get("client") else "",
            "traceparent": req_headers.get("traceparent", ""),
            "parent_span_id": parent_span_id,
            "n_plus_one_threshold": config.n_plus_one_threshold,
            "query_string": redactor.redact_url_params(
                "?" + str(scope.get("query_string", b"") or b"", "latin-1")
            ).lstrip("?"),
        }

        request_body_chunks: list[bytes] = []
        response_body_chunks: list[bytes] = []
        response_headers: dict = {}
        status_code = 500

        capture_req_body = config.capture_request_body and scope.get("method") in {"POST", "PUT", "PATCH"}
        capture_resp_body = config.capture_response_body
        max_body_bytes = int(config.max_body_bytes or 0)
        request_stream_finished = False
        request_body_truncated = False
        response_body_truncated = False
        request_body_size = 0
        response_body_size = 0

        async def wrapped_receive() -> Message:
            nonlocal request_stream_finished, request_body_truncated, request_body_size
            if request_stream_finished:
                return {"type": "http.request", "body": b"", "more_body": False}

            message = await receive()
            if capture_req_body and message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                if body:
                    if max_body_bytes > 0:
                        remaining = max_body_bytes - request_body_size
                        if remaining > 0:
                            kept = body[:remaining]
                            request_body_chunks.append(kept)
                            request_body_size += len(kept)
                        if len(body) > max(remaining, 0):
                            request_body_truncated = True
                    else:
                        request_body_chunks.append(body)
            if message.get("type") == "http.request" and not bool(message.get("more_body", False)):
                request_stream_finished = True
            return message

        async def wrapped_send(message: Message) -> None:
            nonlocal status_code, response_headers, response_body_truncated, response_body_size
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500) or 500)
                headers = message.get("headers") or []
                response_headers = {
                    str(k, "latin-1").lower(): str(v, "latin-1") for k, v in headers
                }
            elif capture_resp_body and message.get("type") == "http.response.body":
                body = message.get("body", b"") or b""
                if body:
                    if max_body_bytes > 0:
                        remaining = max_body_bytes - response_body_size
                        if remaining > 0:
                            kept = body[:remaining]
                            response_body_chunks.append(kept)
                            response_body_size += len(kept)
                        if len(body) > max(remaining, 0):
                            response_body_truncated = True
                    else:
                        response_body_chunks.append(body)
            await send(message)

        exc: BaseException | None = None
        t0 = time.perf_counter()
        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except Exception as e:
            exc = e
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000.0

            from tracegarden.core.fingerprint import annotate_duplicates
            from tracegarden.core.models import TraceRequest

            queries = get_db_queries()
            annotate_duplicates(queries)
            http_calls = get_http_calls()

            safe_req_headers = redactor.redact_headers(req_headers)
            safe_resp_headers = redactor.redact_headers(response_headers)

            if capture_req_body and request_body_chunks:
                try:
                    raw = b"".join(request_body_chunks).decode("utf-8", errors="replace")
                    ct = req_headers.get("content-type", "")
                    metadata["request_body"] = redactor.redact_body(raw, ct)
                    if request_body_truncated:
                        metadata["request_body_truncated"] = True
                except Exception:
                    logger.debug("Failed to capture request body", exc_info=True)

            if capture_resp_body and response_body_chunks:
                try:
                    raw = b"".join(response_body_chunks).decode("utf-8", errors="replace")
                    ct = response_headers.get("content-type", "")
                    metadata["response_body"] = redactor.redact_body(raw, ct)
                    if response_body_truncated:
                        metadata["response_body_truncated"] = True
                except Exception:
                    logger.debug("Failed to capture response body", exc_info=True)

            trace_req = TraceRequest(
                id=new_trace_id(),
                trace_id=trace_id,
                span_id=span_id,
                method=str(scope.get("method", "GET")),
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                started_at=started_at,
                request_headers=safe_req_headers,
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
            reset_runtime(runtime_tokens)

        if exc is not None:
            raise exc


def get_current_trace_id() -> str:
    return _ctx_get_trace_id()


def get_current_span_id() -> str:
    return _ctx_get_span_id()


async def capture_fastapi_db_query(
    sql: str,
    params: object,
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

    redactor = get_runtime_redactor()
    if redactor is None:
        return
    q = DBQuery.create(
        trace_id=trace_id,
        span_id=_ctx_get_span_id(),
        sql=sql,
        fingerprint=fingerprint_sql(sql),
        duration_ms=duration_ms,
        parameters=redactor.redact_db_params(params),
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
    trace_id = _ctx_get_trace_id()
    if not trace_id:
        return

    redactor = get_runtime_redactor()
    if redactor is None:
        return
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
