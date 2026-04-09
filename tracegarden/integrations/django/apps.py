"""
tracegarden.integrations.django.apps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Django AppConfig for TraceGarden.
"""
from __future__ import annotations

import logging

from django.apps import AppConfig  # type: ignore[import]

logger = logging.getLogger(__name__)


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
        from tracegarden import TraceGardenConfig as TGConfig

        # Build config from settings, using TraceGardenConfig defaults for
        # any key not present — avoids hardcoding defaults in two places.
        valid_fields = TGConfig.__dataclass_fields__
        config_values = {k: v for k, v in tg_settings.items() if k in valid_fields}
        config = TGConfig(**config_values)

        from tracegarden.integrations.http import install_http_instrumentation
        install_http_instrumentation()

        if config.ui_token is None:
            logger.warning(
                "TraceGarden: ui_token is not set — the UI at %s is accessible without "
                "authentication. Set TRACEGARDEN['ui_token'] to restrict access.",
                config.ui_prefix,
            )

        # Connect DB query capture signals (side-effect import)
        from . import signals as _signals  # noqa: F401
