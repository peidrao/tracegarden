"""
tracegarden.core.runtime
~~~~~~~~~~~~~~~~~~~~~~~~
Request-scoped runtime objects (storage/redactor) shared across integrations.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from tracegarden.core.redaction import Redactor
    from tracegarden.core.storage import TraceStorage


_storage_var: ContextVar[Optional["TraceStorage"]] = ContextVar(
    "tracegarden_runtime_storage", default=None
)
_redactor_var: ContextVar[Optional["Redactor"]] = ContextVar(
    "tracegarden_runtime_redactor", default=None
)


@dataclass
class RuntimeTokens:
    storage_token: Token
    redactor_token: Token


def bind_runtime(storage: "TraceStorage", redactor: "Redactor") -> RuntimeTokens:
    """Bind storage/redactor to the current execution context."""
    return RuntimeTokens(
        storage_token=_storage_var.set(storage),
        redactor_token=_redactor_var.set(redactor),
    )


def reset_runtime(tokens: RuntimeTokens) -> None:
    """Reset storage/redactor context bindings."""
    _storage_var.reset(tokens.storage_token)
    _redactor_var.reset(tokens.redactor_token)


def get_runtime_storage() -> Optional["TraceStorage"]:
    return _storage_var.get()


def get_runtime_redactor() -> Optional["Redactor"]:
    return _redactor_var.get()
