"""
tracegarden
~~~~~~~~~~~
Developer-first visual backend devtools for Django, Flask, and FastAPI.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .core.storage import TraceStorage
from .core.redaction import Redactor, configure_redactor

try:
    from importlib.metadata import version as _meta_version
    __version__ = _meta_version("tracegarden")
except Exception:
    __version__ = "0.0.0"

logger = logging.getLogger(__name__)

__all__ = [
    "TraceGardenConfig",
    "TraceGarden",
    "setup",
    "__version__",
]


def _default_enabled() -> bool:
    env = (
        os.getenv("TRACEGARDEN_ENV")
        or os.getenv("APP_ENV")
        or os.getenv("ENV")
        or "development"
    ).strip().lower()
    return env in {"development", "dev", "staging", "stage", "test"}


@dataclass
class TraceGardenConfig:
    """
    Central configuration object for TraceGarden.

    Attributes
    ----------
    enabled:
        Master switch. Set to False to disable all instrumentation.
    ui_token:
        Token required to access ``/__tracegarden/`` UI. Set to ``None``
        to disable auth (not recommended in shared environments).
    ui_token_header:
        HTTP header name used to pass the UI token.
    db_path:
        Path to the SQLite database file.
    max_requests:
        Maximum number of requests to retain before pruning oldest.
    redact_headers:
        Additional header names to redact beyond the built-in defaults.
    redact_params:
        Additional parameter/body field names to redact.
    header_allowlist:
        Header names exempted from redaction even if on the denylist.
    n_plus_one_threshold:
        Minimum number of identical query fingerprints to trigger an N+1 warning.
    capture_request_body:
        Whether to capture and store request bodies (after redaction).
    capture_response_body:
        Whether to capture and store response bodies (after redaction).
    max_body_bytes:
        Maximum request/response body size to capture before truncation.
    ui_prefix:
        URL prefix for the TraceGarden UI (default ``/__tracegarden``).
    """

    enabled: bool = field(default_factory=_default_enabled)
    ui_token: Optional[str] = None
    ui_token_header: str = "X-TraceGarden-Token"
    db_path: str = field(
        default_factory=lambda: str(
            Path.home() / ".tracegarden" / "tracegarden.db"
        )
    )
    max_requests: int = 5000
    redact_headers: List[str] = field(default_factory=list)
    redact_params: List[str] = field(default_factory=list)
    header_allowlist: List[str] = field(default_factory=list)
    n_plus_one_threshold: int = 5
    capture_request_body: bool = False
    capture_response_body: bool = False
    max_body_bytes: int = 64 * 1024
    ui_prefix: str = "/__tracegarden"

    def __post_init__(self) -> None:
        if self.max_body_bytes < 0:
            self.max_body_bytes = 0


class TraceGarden:
    """
    Framework-agnostic TraceGarden initialiser.

    Usage (Flask)::

        from tracegarden import TraceGarden
        tg = TraceGarden(app, ui_token="dev-secret")

    Usage (FastAPI)::

        from tracegarden import TraceGarden
        tg = TraceGarden(app, ui_token="dev-secret")

    Usage (standalone / Django via setup())::

        from tracegarden import TraceGarden
        tg = TraceGarden(config=TraceGardenConfig(db_path="/data/tg.db"))
    """

    def __init__(
        self,
        app=None,
        config: Optional[TraceGardenConfig] = None,
        **kwargs,
    ):
        self.config = config or TraceGardenConfig(**kwargs)
        self._storage: Optional[TraceStorage] = None
        self._redactor: Optional[Redactor] = None

        if app is not None:
            self.init_app(app)

    def _bootstrap(self) -> None:
        """Initialize storage and redactor (framework-agnostic setup)."""
        if self._storage is None:
            self._storage = TraceStorage(
                db_path=self.config.db_path,
                max_requests=self.config.max_requests,
            )

        if self._redactor is None:
            self._redactor = configure_redactor(
                header_denylist=set(self.config.redact_headers),
                param_denylist=set(self.config.redact_params),
                header_allowlist=set(self.config.header_allowlist),
            )

        from .integrations.http import install_http_instrumentation
        install_http_instrumentation()

        if self.config.ui_token is None:
            logger.warning(
                "TraceGarden: ui_token is not set — the UI at %s is accessible without "
                "authentication. Set ui_token in TraceGardenConfig to restrict access.",
                self.config.ui_prefix,
            )

    def init_app(self, app=None) -> None:
        """
        Initialise TraceGarden for a given application object.

        Detects the framework by duck-typing the app object.
        Pass ``app=None`` to bootstrap storage and redactor without
        registering any framework middleware (useful when called from ``setup()``).
        """
        if not self.config.enabled:
            return

        self._bootstrap()

        if app is None:
            return

        # Flask detection
        try:
            from flask import Flask as _Flask
            if isinstance(app, _Flask):
                self._init_flask(app)
                return
        except ImportError:
            logger.debug("Flask is not installed; skipping Flask integration detection")

        # FastAPI detection
        try:
            from fastapi import FastAPI as _FastAPI
            if isinstance(app, _FastAPI):
                self._init_fastapi(app)
                return
        except ImportError:
            logger.debug("FastAPI is not installed; skipping FastAPI integration detection")

        raise TypeError(
            "Unsupported app instance. TraceGarden currently supports Flask and FastAPI via "
            "`TraceGarden(app, ...)`; Django uses settings + middleware integration."
        )

    def _init_flask(self, app) -> None:
        from .integrations.flask.extension import init_tracegarden_flask
        init_tracegarden_flask(app, self.config, self._storage, self._redactor)

    def _init_fastapi(self, app) -> None:
        from .integrations.fastapi.middleware import TraceGardenMiddleware
        from .ui.routes import mount_fastapi_router
        app.add_middleware(
            TraceGardenMiddleware,
            config=self.config,
            storage=self._storage,
            redactor=self._redactor,
        )
        mount_fastapi_router(app, config=self.config, storage=self._storage)

    @property
    def storage(self) -> TraceStorage:
        if self._storage is None:
            self._storage = TraceStorage(
                db_path=self.config.db_path,
                max_requests=self.config.max_requests,
            )
        return self._storage


def setup(
    enabled: Optional[bool] = None,
    db_path: Optional[str] = None,
    ui_token: Optional[str] = None,
    ui_token_header: str = "X-TraceGarden-Token",
    redact_headers: Optional[List[str]] = None,
    redact_params: Optional[List[str]] = None,
    header_allowlist: Optional[List[str]] = None,
    n_plus_one_threshold: int = 5,
    max_requests: int = 5000,
    capture_request_body: bool = False,
    capture_response_body: bool = False,
    max_body_bytes: int = 64 * 1024,
    ui_prefix: str = "/__tracegarden",
) -> TraceGarden:
    """
    Convenience function to create and configure a TraceGarden instance.

    Initializes storage and redactor immediately so the instance is ready
    for use without calling ``init_app``. Use this for standalone setup or
    when you want to manually pass storage/config to individual integrations.

    Returns the configured :class:`TraceGarden` instance.
    """
    kwargs = {
        "ui_token": ui_token,
        "ui_token_header": ui_token_header,
        "redact_headers": redact_headers or [],
        "redact_params": redact_params or [],
        "header_allowlist": header_allowlist or [],
        "n_plus_one_threshold": n_plus_one_threshold,
        "max_requests": max_requests,
        "capture_request_body": capture_request_body,
        "capture_response_body": capture_response_body,
        "max_body_bytes": max_body_bytes,
        "ui_prefix": ui_prefix,
    }
    if enabled is not None:
        kwargs["enabled"] = enabled
    if db_path is not None:
        kwargs["db_path"] = db_path
    config = TraceGardenConfig(**kwargs)
    tg = TraceGarden(config=config)
    # Bootstrap without a framework app — initializes storage, redactor, HTTP patching.
    tg.init_app(app=None)
    return tg
