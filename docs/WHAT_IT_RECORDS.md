# What TraceGarden Records (MVP)

TraceGarden stores request-scoped debugging data in local SQLite.

## Request metadata
- HTTP method/path/status/duration
- request + response headers (redacted)
- query string (redacted)
- source metadata (`user_agent`, remote address)
- optional request/response body capture with `max_body_bytes` truncation flags

## Database events
- each SQL statement + normalized fingerprint
- duration and DB vendor
- duplicate count and N+1 detection flags

## Outgoing HTTP events
- method, URL, status, duration
- request/response headers (redacted)
- automatic capture for `requests` and `httpx` when trace context exists

## OTel spans
- imported from OpenTelemetry exporter (`TraceGardenSpanExporter`)
- linked by trace ID to the corresponding request

## Celery task stitching
- queued task event (`PENDING`) at publish time
- worker lifecycle (`STARTED`, `SUCCESS`, `FAILURE`, `RETRY`)
- request-to-task correlation using propagated trace headers

## Export bundle
- single JSON artifact containing request, spans, events, and metadata
