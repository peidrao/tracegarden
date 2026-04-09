"""
tracegarden.integrations.http
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Automatic outgoing HTTP capture for requests/httpx.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from tracegarden.core.context import add_http_call, get_current_trace_context
from tracegarden.core.models import HTTPCall
from tracegarden.core.redaction import get_default_redactor

_PATCHED = False


def install_http_instrumentation() -> None:
    """Patch supported HTTP clients once per process."""
    global _PATCHED
    if _PATCHED:
        return

    _patch_requests()
    _patch_httpx()
    _PATCHED = True


def _patch_requests() -> None:
    try:
        import requests  # type: ignore[import]
    except ImportError:
        return

    if getattr(requests.Session.request, "_tracegarden_patched", False):
        return

    original = requests.Session.request

    def wrapped(self, method: str, url: str, **kwargs):
        ctx = get_current_trace_context()
        if not ctx.get("trace_id"):
            return original(self, method, url, **kwargs)

        redactor = get_default_redactor()
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        response = original(self, method, url, **kwargs)
        duration_ms = (time.perf_counter() - t0) * 1000.0

        call = HTTPCall.create(
            trace_id=ctx["trace_id"],
            method=method.upper(),
            url=redactor.redact_url_params(url),
            status_code=int(getattr(response, "status_code", 0) or 0),
            duration_ms=duration_ms,
            request_headers=redactor.redact_headers(dict(kwargs.get("headers") or {})),
            response_headers=redactor.redact_headers(dict(getattr(response, "headers", {}) or {})),
            started_at=started,
        )
        add_http_call(call)
        return response

    wrapped._tracegarden_patched = True  # type: ignore[attr-defined]
    requests.Session.request = wrapped


def _patch_httpx() -> None:
    try:
        import httpx  # type: ignore[import]
    except ImportError:
        return

    if not getattr(httpx.Client.request, "_tracegarden_patched", False):
        original_sync = httpx.Client.request

        def wrapped_sync(self, method: str, url: Any, *args, **kwargs):
            ctx = get_current_trace_context()
            if not ctx.get("trace_id"):
                return original_sync(self, method, url, *args, **kwargs)

            redactor = get_default_redactor()
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            response = original_sync(self, method, url, *args, **kwargs)
            duration_ms = (time.perf_counter() - t0) * 1000.0

            call = HTTPCall.create(
                trace_id=ctx["trace_id"],
                method=str(method).upper(),
                url=redactor.redact_url_params(str(url)),
                status_code=int(getattr(response, "status_code", 0) or 0),
                duration_ms=duration_ms,
                request_headers=redactor.redact_headers(_headers_to_dict(kwargs.get("headers"))),
                response_headers=redactor.redact_headers(_headers_to_dict(getattr(response, "headers", None))),
                started_at=started,
            )
            add_http_call(call)
            return response

        wrapped_sync._tracegarden_patched = True  # type: ignore[attr-defined]
        httpx.Client.request = wrapped_sync

    if not getattr(httpx.AsyncClient.request, "_tracegarden_patched", False):
        original_async = httpx.AsyncClient.request

        async def wrapped_async(self, method: str, url: Any, *args, **kwargs):
            ctx = get_current_trace_context()
            if not ctx.get("trace_id"):
                return await original_async(self, method, url, *args, **kwargs)

            redactor = get_default_redactor()
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            response = await original_async(self, method, url, *args, **kwargs)
            duration_ms = (time.perf_counter() - t0) * 1000.0

            call = HTTPCall.create(
                trace_id=ctx["trace_id"],
                method=str(method).upper(),
                url=redactor.redact_url_params(str(url)),
                status_code=int(getattr(response, "status_code", 0) or 0),
                duration_ms=duration_ms,
                request_headers=redactor.redact_headers(_headers_to_dict(kwargs.get("headers"))),
                response_headers=redactor.redact_headers(_headers_to_dict(getattr(response, "headers", None))),
                started_at=started,
            )
            add_http_call(call)
            return response

        wrapped_async._tracegarden_patched = True  # type: ignore[attr-defined]
        httpx.AsyncClient.request = wrapped_async


def _headers_to_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value)
    except Exception:
        return {}
