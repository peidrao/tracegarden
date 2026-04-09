"""
tracegarden.integrations.http
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Automatic outgoing HTTP capture for requests/httpx.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from tracegarden.core.context import add_http_call, get_current_trace_context
from tracegarden.core.models import HTTPCall
from tracegarden.core.redaction import Redactor
from tracegarden.core.runtime import get_runtime_redactor

_PATCHED = False
logger = logging.getLogger(__name__)
_FALLBACK_REDACTOR = Redactor()

# References to the originals so we can restore them in uninstall.
_original_requests_request = None
_original_httpx_sync_request = None
_original_httpx_async_request = None


def install_http_instrumentation() -> None:
    """Patch supported HTTP clients once per process."""
    global _PATCHED
    if _PATCHED:
        return

    _patch_requests()
    _patch_httpx()
    _PATCHED = True


def uninstall_http_instrumentation() -> None:
    """Restore the original HTTP client methods (useful in tests)."""
    global _PATCHED, _original_requests_request, _original_httpx_sync_request, _original_httpx_async_request

    if not _PATCHED:
        return

    try:
        import requests  # type: ignore[import]
        if _original_requests_request is not None:
            requests.Session.request = _original_requests_request
            _original_requests_request = None
    except ImportError:
        pass

    try:
        import httpx  # type: ignore[import]
        if _original_httpx_sync_request is not None:
            httpx.Client.request = _original_httpx_sync_request
            _original_httpx_sync_request = None
        if _original_httpx_async_request is not None:
            httpx.AsyncClient.request = _original_httpx_async_request
            _original_httpx_async_request = None
    except ImportError:
        pass

    _PATCHED = False


def _patch_requests() -> None:
    global _original_requests_request
    try:
        import requests  # type: ignore[import]
    except ImportError:
        return

    if getattr(requests.Session.request, "_tracegarden_patched", False):
        return

    _original_requests_request = requests.Session.request
    original = _original_requests_request

    def wrapped(self, method: str, url: str, **kwargs):
        ctx = get_current_trace_context()
        if not ctx.get("trace_id"):
            return original(self, method, url, **kwargs)

        redactor = get_runtime_redactor() or _FALLBACK_REDACTOR
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        status_code = 0
        response_headers = {}
        try:
            response = original(self, method, url, **kwargs)
            status_code = int(getattr(response, "status_code", 0) or 0)
            response_headers = dict(getattr(response, "headers", {}) or {})
            return response
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            call = HTTPCall.create(
                trace_id=ctx["trace_id"],
                method=method.upper(),
                url=redactor.redact_url_params(url),
                status_code=status_code,
                duration_ms=duration_ms,
                request_headers=redactor.redact_headers(dict(kwargs.get("headers") or {})),
                response_headers=redactor.redact_headers(response_headers),
                started_at=started,
            )
            add_http_call(call)

    wrapped._tracegarden_patched = True  # type: ignore[attr-defined]
    requests.Session.request = wrapped  # type: ignore[method-assign]


def _patch_httpx() -> None:
    global _original_httpx_sync_request, _original_httpx_async_request
    try:
        import httpx  # type: ignore[import]
    except ImportError:
        return

    if not getattr(httpx.Client.request, "_tracegarden_patched", False):
        _original_httpx_sync_request = httpx.Client.request
        original_sync = _original_httpx_sync_request

        def wrapped_sync(self, method: str, url: Any, *args, **kwargs):
            ctx = get_current_trace_context()
            if not ctx.get("trace_id"):
                return original_sync(self, method, url, *args, **kwargs)

            redactor = get_runtime_redactor() or _FALLBACK_REDACTOR
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            status_code = 0
            response_headers = {}
            try:
                response = original_sync(self, method, url, *args, **kwargs)
                status_code = int(getattr(response, "status_code", 0) or 0)
                response_headers = _headers_to_dict(getattr(response, "headers", None))
                return response
            finally:
                duration_ms = (time.perf_counter() - t0) * 1000.0
                call = HTTPCall.create(
                    trace_id=ctx["trace_id"],
                    method=str(method).upper(),
                    url=redactor.redact_url_params(str(url)),
                    status_code=status_code,
                    duration_ms=duration_ms,
                    request_headers=redactor.redact_headers(_headers_to_dict(kwargs.get("headers"))),
                    response_headers=redactor.redact_headers(response_headers),
                    started_at=started,
                )
                add_http_call(call)

        wrapped_sync._tracegarden_patched = True  # type: ignore[attr-defined]
        httpx.Client.request = wrapped_sync

    if not getattr(httpx.AsyncClient.request, "_tracegarden_patched", False):
        _original_httpx_async_request = httpx.AsyncClient.request
        original_async = _original_httpx_async_request

        async def wrapped_async(self, method: str, url: Any, *args, **kwargs):
            ctx = get_current_trace_context()
            if not ctx.get("trace_id"):
                return await original_async(self, method, url, *args, **kwargs)

            redactor = get_runtime_redactor() or _FALLBACK_REDACTOR
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            status_code = 0
            response_headers = {}
            try:
                response = await original_async(self, method, url, *args, **kwargs)
                status_code = int(getattr(response, "status_code", 0) or 0)
                response_headers = _headers_to_dict(getattr(response, "headers", None))
                return response
            finally:
                duration_ms = (time.perf_counter() - t0) * 1000.0
                call = HTTPCall.create(
                    trace_id=ctx["trace_id"],
                    method=str(method).upper(),
                    url=redactor.redact_url_params(str(url)),
                    status_code=status_code,
                    duration_ms=duration_ms,
                    request_headers=redactor.redact_headers(_headers_to_dict(kwargs.get("headers"))),
                    response_headers=redactor.redact_headers(response_headers),
                    started_at=started,
                )
                add_http_call(call)

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
        logger.debug("Unable to convert headers to dict", exc_info=True)
        return {}
