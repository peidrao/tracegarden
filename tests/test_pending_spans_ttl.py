"""
Tests for pending_spans TTL cleanup.

Pending spans accumulate when OTel spans arrive before their parent request
is saved (or the request is never completed). Without pruning they grow
without bound; prune_pending_spans() must remove stale entries.
"""
from datetime import datetime, timedelta, timezone

from tracegarden.core.storage import TraceStorage
from tracegarden.core.tracecontext import new_trace_id


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _insert_pending_span(storage: TraceStorage, trace_id: str, started_at: datetime) -> None:
    """Directly insert a pending span at a controlled timestamp."""
    import json

    span = {"trace_id": trace_id, "name": "test-span"}
    with storage._cursor() as cur:
        cur.execute(
            "INSERT INTO pending_spans (trace_id, started_at, span_data) VALUES (?, ?, ?)",
            (trace_id, _iso(started_at), json.dumps(span)),
        )


def _count_pending(storage: TraceStorage) -> int:
    with storage._cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pending_spans")
        return cur.fetchone()[0]


def test_prune_removes_old_spans(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    now = datetime.now(timezone.utc)

    # Two old spans (2 hours ago — beyond the default 1-hour TTL)
    _insert_pending_span(storage, new_trace_id(), now - timedelta(hours=2))
    _insert_pending_span(storage, new_trace_id(), now - timedelta(hours=3))
    # One fresh span (30 minutes ago — within TTL)
    _insert_pending_span(storage, new_trace_id(), now - timedelta(minutes=30))

    assert _count_pending(storage) == 3

    removed = storage.prune_pending_spans(older_than_hours=1)

    assert removed == 2
    assert _count_pending(storage) == 1


def test_prune_keeps_all_fresh_spans(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    now = datetime.now(timezone.utc)

    _insert_pending_span(storage, new_trace_id(), now - timedelta(minutes=10))
    _insert_pending_span(storage, new_trace_id(), now - timedelta(minutes=59))

    removed = storage.prune_pending_spans(older_than_hours=1)

    assert removed == 0
    assert _count_pending(storage) == 2


def test_prune_removes_all_when_all_old(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    now = datetime.now(timezone.utc)

    for hours_ago in [2, 5, 24, 48]:
        _insert_pending_span(storage, new_trace_id(), now - timedelta(hours=hours_ago))

    removed = storage.prune_pending_spans(older_than_hours=1)

    assert removed == 4
    assert _count_pending(storage) == 0


def test_prune_is_called_on_init_db(tmp_path):
    """init_db must prune stale spans so a re-initialised storage is always clean."""
    import json

    db_path = str(tmp_path / "tg.db")
    storage = TraceStorage(db_path=db_path)
    now = datetime.now(timezone.utc)

    # Inject an old span directly into the DB
    old_trace = new_trace_id()
    with storage._cursor() as cur:
        cur.execute(
            "INSERT INTO pending_spans (trace_id, started_at, span_data) VALUES (?, ?, ?)",
            (old_trace, _iso(now - timedelta(hours=3)), json.dumps({"name": "old"})),
        )

    assert _count_pending(storage) == 1

    # Re-creating storage triggers init_db, which should prune the stale span
    storage2 = TraceStorage(db_path=db_path)
    assert _count_pending(storage2) == 0


def test_save_request_clears_matching_pending_spans(tmp_path):
    """When a request arrives after its spans, pending spans are attached and removed."""
    import json
    from datetime import datetime, timezone

    from tracegarden.core.models import TraceRequest
    from tracegarden.core.tracecontext import new_span_id, new_trace_id

    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    trace_id = new_trace_id()
    now = datetime.now(timezone.utc)

    # Insert a fresh pending span for this trace (must be a valid Span dict)
    span = {
        "id": new_trace_id(),
        "trace_id": trace_id,
        "parent_span_id": None,
        "name": "otel-span",
        "kind": "INTERNAL",
        "started_at": now.isoformat(),
        "duration_ms": 5,
        "attributes": {},
        "status": "OK",
    }
    with storage._cursor() as cur:
        cur.execute(
            "INSERT INTO pending_spans (trace_id, started_at, span_data) VALUES (?, ?, ?)",
            (trace_id, now.isoformat(), json.dumps(span)),
        )

    req = TraceRequest(
        id=new_trace_id(),
        trace_id=trace_id,
        span_id=new_span_id(),
        method="GET",
        path="/traced",
        status_code=200,
        duration_ms=10.0,
        started_at=now,
        request_headers={},
        response_headers={},
    )
    storage.save_request(req)

    # Pending span must be gone
    assert _count_pending(storage) == 0

    # And attached to the request
    saved = storage.get_request(req.id)
    assert saved is not None
    assert any(s.name == "otel-span" for s in saved.spans)
