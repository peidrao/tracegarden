<p align="center">
  <img src="docs/screenshots/tracegarden.png" alt="TraceGarden" width="200"/>
</p>

# TraceGarden

Developer-first visual backend devtools for Django, Flask, and FastAPI.

TraceGarden captures the full request lifecycle — DB queries, outgoing HTTP, OpenTelemetry spans, and **Celery tasks stitched back to the web request that triggered them** — into local SQLite and serves a built-in UI at `/__tracegarden`.

## Why TraceGarden?

**Django Debug Toolbar is great for SQL.** TraceGarden goes further:

| Capability | Django Debug Toolbar | TraceGarden |
|---|---|---|
| SQL queries + N+1 detection | ✅ | ✅ |
| Outgoing HTTP calls inspector | ❌ | ✅ |
| Celery task timeline | ❌ | ✅ |
| **Celery task ↔ parent request stitching** | ❌ | ✅ |
| OpenTelemetry span ingestion | ❌ | ✅ |
| Flask / FastAPI support | ❌ | ✅ |
| Trace bundle export (JSON) | ❌ | ✅ |

The key feature is **Celery stitching**: when a web request enqueues a background task, TraceGarden links the task's full lifecycle (PENDING → STARTED → SUCCESS/FAILURE, with duration and result) back to the originating request in the UI. No external infrastructure required.

## Features

- WSGI + ASGI support (`Django`, `Flask`, `FastAPI`)
- DB query fingerprinting, grouping, duplicate and N+1 detection
- Outgoing HTTP inspector (`requests` / `httpx`)
- Celery stitching (`web request → queued task → worker state`)
- OTel-native span ingestion into local TraceGarden UI
- Trace bundle export (single JSON artifact)
- Redaction by default: auth tokens, passwords, cookies never reach SQLite
- UI token protection

## Installation

```bash
pip install tracegarden
# extras
pip install tracegarden[django]
pip install tracegarden[flask]
pip install tracegarden[fastapi]
pip install tracegarden[celery]
```

## Quick Start

### Flask (two lines)

```python
from flask import Flask
from tracegarden import TraceGarden

app = Flask(__name__)
TraceGarden(app, ui_token="dev-secret")
```

### FastAPI (two lines)

```python
from fastapi import FastAPI
from tracegarden import TraceGarden

app = FastAPI()
TraceGarden(app, ui_token="dev-secret")
```

### Django

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "tracegarden.integrations.django",
]

MIDDLEWARE = [
    "tracegarden.integrations.django.middleware.TraceGardenMiddleware",
    ...,
]

TRACEGARDEN = {
    "enabled": True,
    "ui_token": "dev-secret",
}

# urls.py
from tracegarden.ui.routes import mount_django_urls
urlpatterns = mount_django_urls() + urlpatterns
```

### Celery stitching

Add one call after your Celery app is created:

```python
# celery.py
from celery import Celery
from tracegarden.integrations.celery.signals import connect_signals
from tracegarden.core.storage import TraceStorage

app = Celery("myproject")
storage = TraceStorage()
connect_signals(storage=storage)
```

TraceGarden will automatically propagate trace IDs through task headers and correlate each task back to the web request that dispatched it — including task duration, result, and failure details.

## UI Access

Default route: `/__tracegarden/`

Pass token using one of:
- header: `X-TraceGarden-Token: dev-secret`
- query param: `?token=dev-secret`
- cookie: `tg_token=dev-secret`

## Screenshots

![Request list](docs/screenshots/request-list.svg)
![Request detail](docs/screenshots/request-detail.svg)

## OpenTelemetry

```python
from tracegarden.otel.setup import setup_otel

setup_otel(
    service_name="my-api",
    also_export_to_tracegarden=True,
)
```

## Body capture

Request and response bodies are **not captured by default**. Enable with:

```python
TraceGarden(app, ui_token="dev-secret", capture_request_body=True, capture_response_body=True)
```

Bodies are truncated at `max_body_bytes` (default 64 KB) and redacted before storage.

## What gets recorded

See [docs/WHAT_IT_RECORDS.md](docs/WHAT_IT_RECORDS.md).

## End-to-end examples

See [examples/README.md](examples/README.md).

## License

MIT
