# TraceGarden — Design Document

## 1. Goals and Non-Goals

### Goals

- **Frictionless local observability.** A developer should be able to add one middleware line and immediately see every request's full lifecycle — DB queries, outgoing HTTP calls, background tasks, and OTel spans — in a browser UI without standing up any external infrastructure.
- **N+1 detection.** Automatically fingerprint and group SQL queries per request, and surface N+1 patterns with inline warnings before they reach production.
- **Privacy by default.** Sensitive data (auth tokens, cookies, passwords) must be redacted before storage. Developers in trusted local environments can selectively expand the allowlist; production use should leave defaults intact.
- **Celery trace stitching.** A task enqueued during a web request should appear in that request's detail view, correlated by trace ID propagated through task headers.
- **OTel compatibility.** TraceGarden should operate as a SpanExporter so teams already using OpenTelemetry can route spans into the local UI with zero duplication of instrumentation effort.
- **Framework agnostic core.** The core data models, storage, and UI logic must be framework-independent. Framework integrations are thin adapters over the core.

### Non-Goals

- **Production deployment.** TraceGarden is a development tool. It has no auth hardening, no multi-tenant isolation, no high-throughput path.
- **Distributed tracing across services.** TraceGarden correlates tasks within a single application. Cross-service distributed tracing belongs to a full OTel backend (Jaeger, Tempo).
- **Log aggregation.** TraceGarden captures structured events, not free-form log lines.
- **APM alerting or SLO tracking.** No alerting, no time-series metrics, no dashboards with aggregations over time windows.
- **Database query rewriting or optimization advice.** TraceGarden detects N+1 patterns but does not suggest ORM alternatives.

---

## 2. User Stories

**US-1 — The Django developer debugging slow endpoints.**
> "As a Django developer, I want to open `/__tracegarden/` after hitting a slow API endpoint and immediately see which SQL queries ran, how many were duplicates, and what the N+1 offender is, so I can fix the ORM query before opening a PR."

**US-2 — The backend engineer auditing third-party calls.**
> "As a backend engineer, I want to see every outgoing HTTP call made during a request — URL, status, duration — so I can verify that my service isn't making redundant calls to upstream APIs."

**US-3 — The async developer tracing Celery task chains.**
> "As a developer using Celery, I want to click on a web request in the UI and see which background tasks it enqueued, their current state, and how long they ran, so I can understand the full cost of a user-facing action including its async work."

**US-4 — The security-conscious team lead.**
> "As a team lead, I want TraceGarden to redact authorization headers and password fields by default, so that even if a developer accidentally leaves it on in a staging environment, no credentials are persisted to the local SQLite file."

---

## 3. Architecture Overview

```
  HTTP Request
       │
       ▼
┌─────────────────────────┐
│  Framework Middleware   │  Django / Flask / FastAPI ASGI
│  (per-framework thin    │  • Generates request UUID
│   adapter)              │  • Starts timer
│                         │  • Sets thread-local / context-var
└──────────┬──────────────┘
           │ hooks into
           ▼
┌─────────────────────────┐
│    Signal / Hook Layer  │  Django DB signals, urllib patch,
│                         │  Celery task signals
│  • DBQuery capture      │
│  • HTTPCall capture     │
│  • CeleryTask events    │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│      Redactor           │  Applied before any persistence
│  (core/redaction.py)    │  • Header denylist
│                         │  • Param/body denylist
│                         │  • Allowlist overrides
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  SQL Fingerprinter      │  core/fingerprint.py
│  + N+1 Detector         │  • Normalize literals → ?
│                         │  • Group by fingerprint
│                         │  • Flag count > threshold
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│    TraceStorage         │  core/storage.py
│    (SQLite)             │  • trace_requests table
│                         │  • celery_tasks table
│                         │  • JSON-serialized sub-objects
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│    UI Route Handlers    │  ui/routes.py
│    (framework-agnostic) │  • Token auth
│                         │  • Jinja2 templates
│                         │  • JSON export
└─────────────────────────┘
           │
           ▼
     Browser  /__tracegarden/
```

**OTel path (optional):**
`setup_otel()` installs a `TraceGardenSpanExporter` as an additional processor alongside whatever OTLP exporter the team already uses. The exporter converts OTel `ReadableSpan` objects into `tracegarden.Span` records and writes them to storage, linking them to the in-flight `TraceRequest` via trace ID.

---

## 4. Data Model

### TraceRequest
The root record for a single HTTP request/response cycle.

| Field | Type | Notes |
|---|---|---|
| `id` | `str` (UUID4) | Primary key |
| `trace_id` | `str` | OTel-compatible W3C trace ID (hex) |
| `span_id` | `str` | Root span ID |
| `method` | `str` | GET / POST / … |
| `path` | `str` | URL path (no query string) |
| `status_code` | `int` | HTTP response status |
| `duration_ms` | `float` | Total request duration |
| `started_at` | `datetime` | UTC |
| `request_headers` | `dict` | Post-redaction |
| `response_headers` | `dict` | Post-redaction |
| `db_queries` | `List[DBQuery]` | Embedded |
| `http_calls` | `List[HTTPCall]` | Embedded |
| `spans` | `List[Span]` | Embedded |
| `celery_tasks` | `List[CeleryTask]` | Resolved at read time |
| `metadata` | `dict` | Arbitrary extra context |

### DBQuery
Captures a single database query execution.

| Field | Type | Notes |
|---|---|---|
| `sql` | `str` | Raw SQL |
| `fingerprint` | `str` | Normalized SQL for grouping |
| `duration_ms` | `float` | |
| `parameters` | `list` | Redacted if sensitive |
| `db_vendor` | `str` | `sqlite` / `postgres` / `mysql` |
| `is_duplicate` | `bool` | True if fingerprint seen >1× in request |
| `duplicate_count` | `int` | How many times this fingerprint ran |

### HTTPCall
An outgoing HTTP request made during request processing.

| Field | Type | Notes |
|---|---|---|
| `method` | `str` | |
| `url` | `str` | |
| `status_code` | `int` | |
| `duration_ms` | `float` | |
| `request_headers` | `dict` | Redacted |
| `response_headers` | `dict` | Redacted |

### Span
An OTel span associated with the request.

| Field | Type | Notes |
|---|---|---|
| `parent_span_id` | `Optional[str]` | For tree reconstruction |
| `name` | `str` | |
| `kind` | `str` | `SERVER` / `CLIENT` / `INTERNAL` / … |
| `duration_ms` | `float` | |
| `attributes` | `dict` | |
| `status` | `str` | `OK` / `ERROR` / `UNSET` |

### CeleryTask
A background task enqueued during or linked to a web request.

| Field | Type | Notes |
|---|---|---|
| `task_id` | `str` | Celery task UUID |
| `trace_id` | `str` | OTel trace ID from task headers |
| `parent_trace_id` | `str` | Web request trace ID (stitching key) |
| `task_name` | `str` | Dotted Python path |
| `state` | `str` | `PENDING` / `STARTED` / `SUCCESS` / `FAILURE` |
| `queue` | `str` | |
| `enqueued_at` | `datetime` | |
| `started_at` | `Optional[datetime]` | |
| `completed_at` | `Optional[datetime]` | |
| `duration_ms` | `Optional[float]` | |
| `args` / `kwargs` | `list` / `dict` | Redacted |
| `result` | `Optional[str]` | Serialized return value |
| `exception` | `Optional[str]` | Exception string on failure |

---

## 5. Privacy Model

### Redaction by default

TraceGarden applies redaction at the boundary between capture and storage — nothing sensitive is ever written to SQLite.

**Default header denylist** (case-insensitive):
`authorization`, `cookie`, `set-cookie`, `x-api-key`, `x-auth-token`

**Default parameter/body field denylist**:
`password`, `passwd`, `secret`, `token`, `api_key`, `access_token`, `refresh_token`, `private_key`

Redacted values are replaced with the string `[REDACTED]`.

### Allowlist overrides

In fully trusted local environments where seeing full header values aids debugging, individual headers can be moved to an allowlist:

```python
TraceGardenConfig(header_allowlist=["authorization"])
```

An allowlisted key is excluded from the denylist check even if it appears in `SENSITIVE_HEADERS`. This is intentionally opt-in per-key rather than a blanket "disable redaction" flag.

### JSON body redaction

For `application/json` request/response bodies, the `Redactor` recursively traverses the parsed JSON and redacts any key whose name matches the param denylist. For non-JSON bodies (form data, binary), the entire body is replaced with `[BODY REDACTED]`.

### SQL parameters

Query bind parameters are passed through the param denylist redactor before storage. Raw SQL is stored as-is (it should not contain literal credential values in parameterized queries; if it does, that is a separate application bug).
