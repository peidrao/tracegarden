"""
tracegarden.integrations.django.middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Django WSGI middleware for TraceGarden request capture.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from django.conf import settings  # type: ignore[import]
from django.http import HttpRequest, HttpResponse  # type: ignore[import]

from tracegarden import TraceGardenConfig
from tracegarden.core.redaction import Redactor
from tracegarden.core.runtime import bind_runtime, reset_runtime
from tracegarden.core.storage import TraceStorage
from tracegarden.core.tracecontext import new_span_id, new_trace_id, parse_traceparent

logger = logging.getLogger(__name__)


def _get_tg_config():
    """Return the TRACEGARDEN settings dict (with defaults)."""
    return getattr(settings, "TRACEGARDEN", {})


def _get_db_vendor() -> str:
    """Detect the primary database vendor from Django settings."""
    try:
        dbs = getattr(settings, "DATABASES", {})
        default_db = dbs.get("default", {})
        engine = default_db.get("ENGINE", "")
        if "postgres" in engine or "psycopg" in engine:
            return "postgres"
        if "mysql" in engine:
            return "mysql"
        if "sqlite" in engine:
            return "sqlite"
    except Exception:
        logger.debug("Failed to detect Django DB vendor from settings", exc_info=True)
    return "unknown"


class TraceGardenMiddleware:
    """
    Django middleware that captures request/response data and stores a
    TraceRequest in the configured SQLite backend.

    Add to MIDDLEWARE *before* other middleware so timing is accurate::

        MIDDLEWARE = [
            "tracegarden.integrations.django.middleware.TraceGardenMiddleware",
            ...
        ]
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self._tg_settings = _get_tg_config()
        self._enabled = self._tg_settings.get("enabled", True)
        self._db_vendor = _get_db_vendor()
        self._n_plus_one_threshold = self._tg_settings.get("n_plus_one_threshold", 5)
        self._config = TraceGardenConfig(
            **{
                k: v
                for k, v in self._tg_settings.items()
                if k in TraceGardenConfig.__dataclass_fields__
            }
        )
        self._storage = TraceStorage(
            db_path=self._config.db_path,
            max_requests=self._config.max_requests,
        )
        self._redactor = Redactor(
            header_denylist=set(self._config.redact_headers),
            param_denylist=set(self._config.redact_params),
            header_allowlist=set(self._config.header_allowlist),
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        ui_prefix = self._tg_settings.get("ui_prefix", "/__tracegarden")
        if not self._enabled or request.path.startswith(ui_prefix):
            return self.get_response(request)

        from tracegarden.core.context import (
            clear_request_context,
            get_db_queries,
            get_http_calls,
            reset_events,
            set_request_context,
        )
        from tracegarden.core.fingerprint import annotate_duplicates
        from tracegarden.core.models import TraceRequest
        from .signals import _record_query

        redactor = self._redactor
        storage = self._storage

        # Build trace context
        incoming = parse_traceparent(request.META.get("HTTP_TRACEPARENT"))
        trace_id = incoming[0] if incoming else new_trace_id()
        parent_span_id = incoming[1] if incoming else ""
        span_id = new_span_id()
        started_at = datetime.now(timezone.utc)
        set_request_context(trace_id=trace_id, span_id=span_id, db_vendor=self._db_vendor)
        reset_events()
        runtime_tokens = bind_runtime(storage, redactor)

        # Collect request headers
        req_headers: dict = {}
        for key, value in request.META.items():
            if key.startswith("HTTP_"):
                header_name = key[5:].replace("_", "-").lower()
                req_headers[header_name] = value
            elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                header_name = key.replace("_", "-").lower()
                req_headers[header_name] = value

        safe_req_headers = redactor.redact_headers(req_headers)

        # Install DB execute wrapper
        try:
            from django.db import connection  # type: ignore[import]
            connection.execute_wrappers.append(_record_query)
        except Exception:
            logger.debug("Failed to install Django DB execute wrapper", exc_info=True)

        response: HttpResponse | None = None
        exc: BaseException | None = None
        t0 = time.perf_counter()
        try:
            response = self.get_response(request)
        except Exception as e:
            exc = e
        finally:
            # Always remove DB execute wrapper.
            try:
                from django.db import connection  # type: ignore[import]
                if _record_query in connection.execute_wrappers:
                    connection.execute_wrappers.remove(_record_query)
            except Exception:
                logger.debug("Failed to remove Django DB execute wrapper", exc_info=True)

            duration_ms = (time.perf_counter() - t0) * 1000.0

            # Collect response headers (only if we got a response)
            resp_headers: dict = {}
            if response is not None:
                for key, value in response.items():
                    resp_headers[key.lower()] = value
            safe_resp_headers = redactor.redact_headers(resp_headers)

            queries = get_db_queries()
            annotate_duplicates(queries)
            http_calls = get_http_calls()

            status_code = response.status_code if response is not None else 500

            metadata: dict = {
                "user_agent": req_headers.get("user-agent", ""),
                "remote_addr": request.META.get("REMOTE_ADDR", ""),
                "traceparent": request.META.get("HTTP_TRACEPARENT", ""),
                "parent_span_id": parent_span_id,
                "n_plus_one_threshold": self._n_plus_one_threshold,
                "query_string": redactor.redact_url_params(
                    "?" + request.META.get("QUERY_STRING", "")
                ).lstrip("?"),
            }

            capture_req_body = self._tg_settings.get("capture_request_body", False)
            capture_resp_body = self._tg_settings.get("capture_response_body", False)
            max_body_bytes = int(self._tg_settings.get("max_body_bytes", 64 * 1024) or 0)

            if capture_req_body and request.method in ("POST", "PUT", "PATCH"):
                try:
                    raw_body = request.body or b""
                    truncated = max_body_bytes > 0 and len(raw_body) > max_body_bytes
                    if max_body_bytes > 0:
                        raw_body = raw_body[:max_body_bytes]
                    raw = raw_body.decode("utf-8", errors="replace")
                    ct = request.META.get("CONTENT_TYPE", "")
                    metadata["request_body"] = redactor.redact_body(raw, ct)
                    if truncated:
                        metadata["request_body_truncated"] = True
                except Exception:
                    logger.debug("Failed to capture request body", exc_info=True)

            if capture_resp_body and response is not None and hasattr(response, "content"):
                try:
                    raw_body = response.content or b""
                    truncated = max_body_bytes > 0 and len(raw_body) > max_body_bytes
                    if max_body_bytes > 0:
                        raw_body = raw_body[:max_body_bytes]
                    raw = raw_body.decode("utf-8", errors="replace")
                    ct = response.get("content-type", "")
                    metadata["response_body"] = redactor.redact_body(raw, ct)
                    if truncated:
                        metadata["response_body_truncated"] = True
                except Exception:
                    logger.debug("Failed to capture response body", exc_info=True)

            trace_req = TraceRequest(
                id=new_trace_id(),
                trace_id=trace_id,
                span_id=span_id,
                method=request.method,
                path=request.path,
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

        return response  # type: ignore[return-value]
