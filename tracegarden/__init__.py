"""
tracegarden
~~~~~~~~~~~
Developer-first visual backend devtools for Django, Flask, and FastAPI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .core.storage import TraceStorage, get_default_storage, set_default_storage
from .core.redaction import Redactor, configure_redactor

__version__ = "0.1.0"
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
    ui_prefix:
        URL prefix for the TraceGarden UI (default ``/__tracegarden``).
    """

    enabled: bool = field(default_factory=_default_enabled)
    ui_token: Optional[str] = None
    ui_token_header: str = "X-TraceGarden-Token"
    db_path: str = "/tmp/tracegarden.db"
    max_requests: int = 5000
    redact_headers: List[str] = field(default_factory=list)
    redact_params: List[str] = field(default_factory=list)
    header_allowlist: List[str] = field(default_factory=list)
    n_plus_one_threshold: int = 5
    capture_request_body: bool = False
    capture_response_body: bool = False
    ui_prefix: str = "/__tracegarden"


class TraceGarden:
    """
    Framework-agnostic TraceGarden initialiser.

    Usage (Flask)::

        from tracegarden import TraceGarden
        tg = TraceGarden(app, ui_token="dev-secret")

    Usage (standalone)::

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

    def init_app(self, app) -> None:
        """
        Initialise TraceGarden for a given application object.

        Detects the framework by duck-typing the app object.
        """
        if not self.config.enabled:
            return

        self._storage = TraceStorage(
            db_path=self.config.db_path,
            max_requests=self.config.max_requests,
        )
        set_default_storage(self._storage)

        self._redactor = configure_redactor(
            header_denylist=set(self.config.redact_headers),
            param_denylist=set(self.config.redact_params),
            header_allowlist=set(self.config.header_allowlist),
        )
        from .integrations.http import install_http_instrumentation
        install_http_instrumentation()

        # Flask detection
        try:
            from flask import Flask as _Flask
            if isinstance(app, _Flask):
                self._init_flask(app)
                return
        except ImportError:
            pass

        # FastAPI detection
        try:
            from fastapi import FastAPI as _FastAPI
            if isinstance(app, _FastAPI):
                self._init_fastapi(app)
                return
        except ImportError:
            pass

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
            self._storage = get_default_storage(self.config.db_path)
        return self._storage


def setup(
    db_path: str = "/tmp/tracegarden.db",
    ui_token: Optional[str] = None,
    ui_token_header: str = "X-TraceGarden-Token",
    redact_headers: Optional[List[str]] = None,
    redact_params: Optional[List[str]] = None,
    header_allowlist: Optional[List[str]] = None,
    n_plus_one_threshold: int = 5,
) -> TraceGarden:
    """
    Convenience function to create and configure a TraceGarden instance.

    Returns the configured instance which can be used to call ``init_app``
    later, or ignored if using Django (which is configured via settings).
    """
    config = TraceGardenConfig(
        db_path=db_path,
        ui_token=ui_token,
        ui_token_header=ui_token_header,
        redact_headers=redact_headers or [],
        redact_params=redact_params or [],
        header_allowlist=header_allowlist or [],
        n_plus_one_threshold=n_plus_one_threshold,
    )
    return TraceGarden(config=config)
