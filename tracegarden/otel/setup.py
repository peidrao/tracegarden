"""
tracegarden.otel.setup
~~~~~~~~~~~~~~~~~~~~~~~
OpenTelemetry TracerProvider setup with optional TraceGarden span export.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

logger = logging.getLogger(__name__)

from opentelemetry import trace  # type: ignore[import]
from opentelemetry.sdk.resources import Resource  # type: ignore[import]
from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import]
from opentelemetry.sdk.trace.export import (  # type: ignore[import]
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


def setup_otel(
    service_name: str,
    also_export_to_tracegarden: bool = True,
    storage=None,
) -> TracerProvider:
    """
    Configure an OpenTelemetry TracerProvider.

    Parameters
    ----------
    service_name:
        The logical name of the service (e.g. ``"my-api"``).
    also_export_to_tracegarden:
        If True, also add a :class:`TraceGardenSpanExporter` so spans flow
        into the local TraceGarden UI.
    storage:
        Override the TraceStorage instance used by the TraceGarden exporter.
        Defaults to the process-wide default storage.

    Returns
    -------
    TracerProvider
        The configured provider (also set as the global OTel provider).
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if also_export_to_tracegarden:
        tg_exporter = TraceGardenSpanExporter(storage=storage)
        provider.add_span_processor(BatchSpanProcessor(tg_exporter))

    trace.set_tracer_provider(provider)
    return provider


class TraceGardenSpanExporter(SpanExporter):
    """
    OTel SpanExporter that writes spans into the TraceGarden SQLite storage.

    This exporter converts :class:`opentelemetry.sdk.trace.ReadableSpan`
    objects into :class:`~tracegarden.core.models.Span` records and appends
    them to the matching :class:`~tracegarden.core.models.TraceRequest` via
    the trace ID.
    """

    def __init__(self, storage=None):
        self._storage = storage

    def _get_storage(self):
        if self._storage is not None:
            return self._storage
        from tracegarden.core.storage import TraceStorage

        self._storage = TraceStorage()
        return self._storage

    def export(self, spans: Sequence) -> SpanExportResult:
        """Convert and store OTel spans."""
        storage = self._get_storage()
        for otel_span in spans:
            try:
                tg_span_dict = self._convert_span(otel_span)
                trace_id_hex = self._format_trace_id(
                    otel_span.get_span_context().trace_id
                )
                storage.add_span_to_request(trace_id_hex, tg_span_dict)
            except Exception:
                # Never let exporter errors bubble up and break the app
                logger.debug("Failed to export span to TraceGarden", exc_info=True)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_trace_id(trace_id_int: int) -> str:
        return format(trace_id_int, "032x")

    @staticmethod
    def _format_span_id(span_id_int: int) -> str:
        return format(span_id_int, "016x")

    @staticmethod
    def _ns_to_datetime(ns: int) -> datetime:
        return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)

    def _convert_span(self, otel_span) -> dict:
        ctx = otel_span.get_span_context()
        parent = otel_span.parent

        started_at = self._ns_to_datetime(otel_span.start_time)
        end_ns = otel_span.end_time or otel_span.start_time
        duration_ms = (end_ns - otel_span.start_time) / 1e6

        # Map OTel StatusCode to string
        try:
            from opentelemetry.trace import StatusCode  # type: ignore[import]
            status_map = {
                StatusCode.OK: "OK",
                StatusCode.ERROR: "ERROR",
                StatusCode.UNSET: "UNSET",
            }
            status_str = status_map.get(otel_span.status.status_code, "UNSET")
        except Exception:
            status_str = "UNSET"

        # Map SpanKind to string
        try:
            kind_str = otel_span.kind.name  # e.g. "SERVER", "CLIENT"
        except Exception:
            kind_str = "INTERNAL"

        return {
            "id": self._format_span_id(ctx.span_id),
            "trace_id": self._format_trace_id(ctx.trace_id),
            "parent_span_id": (
                self._format_span_id(parent.span_id)
                if parent and parent.span_id
                else None
            ),
            "name": otel_span.name,
            "kind": kind_str,
            "started_at": started_at.isoformat(),
            "duration_ms": duration_ms,
            "attributes": dict(otel_span.attributes or {}),
            "status": status_str,
        }
