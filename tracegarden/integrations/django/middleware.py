"""
tracegarden.integrations.django.middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Django WSGI middleware for TraceGarden request capture.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from django.conf import settings  # type: ignore[import]
from django.http import HttpRequest, HttpResponse  # type: ignore[import]


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
        pass
    return "sqlite"


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

    def __call__(self, request: HttpRequest) -> HttpResponse:
        ui_prefix = self._tg_settings.get("ui_prefix", "/__tracegarden")
        if not self._enabled or request.path.startswith(ui_prefix):
            return self.get_response(request)

        from tracegarden.core.models import TraceRequest
        from tracegarden.core.redaction import get_default_redactor
        from tracegarden.core.fingerprint import annotate_duplicates
        from tracegarden.core.storage import get_default_storage
        from tracegarden.core.tracecontext import parse_traceparent
        from tracegarden.core.context import (
            set_request_context,
            clear_request_context,
            reset_events,
            get_db_queries,
            get_http_calls,
        )
        from .signals import _record_query

        redactor = get_default_redactor()
        storage = get_default_storage()

        # Build trace context
        incoming = parse_traceparent(request.META.get("HTTP_TRACEPARENT"))
        trace_id = incoming[0] if incoming else str(uuid.uuid4()).replace("-", "")
        span_id = str(uuid.uuid4()).replace("-", "")[:16]
        started_at = datetime.now(timezone.utc)
        set_request_context(trace_id=trace_id, span_id=span_id, db_vendor=self._db_vendor)
        reset_events()

        # Collect request headers
        req_headers = {}
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
            pass

        t0 = time.perf_counter()
        try:
            response: HttpResponse = self.get_response(request)
        except Exception:
            clear_request_context()
            raise
        finally:
            # Remove DB execute wrapper even when app raises.
            try:
                from django.db import connection  # type: ignore[import]
                if _record_query in connection.execute_wrappers:
                    connection.execute_wrappers.remove(_record_query)
            except Exception:
                pass
        duration_ms = (time.perf_counter() - t0) * 1000.0

        # Collect response headers
        resp_headers = {}
        for key, value in response.items():
            resp_headers[key.lower()] = value
        safe_resp_headers = redactor.redact_headers(resp_headers)

        # Gather queries and annotate duplicates
        queries = get_db_queries()
        annotate_duplicates(queries)
        http_calls = get_http_calls()

        # Build TraceRequest
        trace_req = TraceRequest(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            span_id=span_id,
            method=request.method,
            path=request.path,
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
                "remote_addr": request.META.get("REMOTE_ADDR", ""),
                "traceparent": request.META.get("HTTP_TRACEPARENT", ""),
                "query_string": redactor.redact_url_params(
                    "?" + request.META.get("QUERY_STRING", "")
                ).lstrip("?"),
            },
        )

        storage.save_request(trace_req)
        clear_request_context()

        return response
