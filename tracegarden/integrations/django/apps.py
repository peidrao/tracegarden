"""
tracegarden.integrations.django.apps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Django AppConfig for TraceGarden.
"""
from __future__ import annotations

from django.apps import AppConfig  # type: ignore[import]


class TraceGardenConfig(AppConfig):
    name = "tracegarden.integrations.django"
    label = "tracegarden"
    verbose_name = "TraceGarden"

    def ready(self) -> None:
        """Connect Django DB signals and read project settings."""
        from django.conf import settings  # type: ignore[import]

        tg_settings: dict = getattr(settings, "TRACEGARDEN", {})
        if not tg_settings.get("enabled", True):
            return

        # Import here to avoid premature Django setup during import
        from tracegarden.core.storage import TraceStorage, set_default_storage
        from tracegarden.core.redaction import configure_redactor
        from tracegarden import TraceGardenConfig as TGConfig

        config = TGConfig(
            enabled=tg_settings.get("enabled", True),
            ui_token=tg_settings.get("ui_token"),
            ui_token_header=tg_settings.get("ui_token_header", "X-TraceGarden-Token"),
            db_path=tg_settings.get("db_path", "/tmp/tracegarden.db"),
            max_requests=tg_settings.get("max_requests", 5000),
            redact_headers=tg_settings.get("redact_headers", []),
            redact_params=tg_settings.get("redact_params", []),
            header_allowlist=tg_settings.get("header_allowlist", []),
            n_plus_one_threshold=tg_settings.get("n_plus_one_threshold", 5),
            ui_prefix=tg_settings.get("ui_prefix", "/__tracegarden"),
        )

        storage = TraceStorage(db_path=config.db_path, max_requests=config.max_requests)
        set_default_storage(storage)

        configure_redactor(
            header_denylist=set(config.redact_headers),
            param_denylist=set(config.redact_params),
            header_allowlist=set(config.header_allowlist),
        )
        from tracegarden.integrations.http import install_http_instrumentation
        install_http_instrumentation()

        # Connect DB query capture signals
        from . import signals as _signals  # noqa: F401 — side-effect import
