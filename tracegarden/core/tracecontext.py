"""
tracegarden.core.tracecontext
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
W3C trace-context helpers.
"""
from __future__ import annotations

import re
import uuid
from typing import Optional, Tuple

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<parent_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def parse_traceparent(value: str | None) -> Optional[Tuple[str, str]]:
    """Return ``(trace_id, parent_span_id)`` if valid; otherwise ``None``."""
    if not value:
        return None
    match = _TRACEPARENT_RE.match(value.strip().lower())
    if not match:
        return None

    trace_id = match.group("trace_id")
    parent_id = match.group("parent_id")

    # W3C invalid all-zero IDs
    if trace_id == "0" * 32 or parent_id == "0" * 16:
        return None
    return trace_id, parent_id
