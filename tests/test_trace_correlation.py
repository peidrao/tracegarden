from datetime import datetime, timezone

from tracegarden.core.models import CeleryTask, DBQuery, Span, TraceRequest
from tracegarden.core.storage import TraceStorage
from tracegarden.core.tracecontext import parse_traceparent


def test_parse_traceparent_valid_and_invalid():
    value = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    parsed = parse_traceparent(value)
    assert parsed is not None
    assert parsed[0] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parsed[1] == "00f067aa0ba902b7"

    assert parse_traceparent("bad") is None
    assert parse_traceparent("00-00000000000000000000000000000000-00f067aa0ba902b7-01") is None


def test_span_is_appended_to_matching_trace(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    req = TraceRequest.create(method="GET", path="/demo", trace_id="a" * 32, span_id="b" * 16)
    req.status_code = 200
    req.duration_ms = 42.0
    storage.save_request(req)

    span = Span.create(
        trace_id="a" * 32,
        name="db.query",
        kind="CLIENT",
        span_id="c" * 16,
        started_at=datetime.now(timezone.utc),
        duration_ms=3.2,
    )
    storage.add_span_to_request("a" * 32, span.to_dict())

    saved = storage.get_request(req.id)
    assert saved is not None
    assert len(saved.spans) == 1
    assert saved.spans[0].id == "c" * 16


def test_span_is_buffered_when_request_not_persisted_yet(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    trace_id = "f" * 32

    span = Span.create(
        trace_id=trace_id,
        name="worker.step",
        kind="INTERNAL",
        span_id="1" * 16,
        started_at=datetime.now(timezone.utc),
        duration_ms=2.5,
    )
    storage.add_span_to_request(trace_id, span.to_dict())

    req = TraceRequest.create(method="GET", path="/late", trace_id=trace_id, span_id="2" * 16)
    req.status_code = 200
    req.duration_ms = 5.0
    storage.save_request(req)

    saved = storage.get_request(req.id)
    assert saved is not None
    assert len(saved.spans) == 1
    assert saved.spans[0].id == "1" * 16


def test_celery_task_stitches_back_to_request(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    req = TraceRequest.create(method="POST", path="/demo", trace_id="d" * 32, span_id="e" * 16)
    req.status_code = 202
    req.duration_ms = 18.0
    storage.save_request(req)

    task = CeleryTask.create(
        task_id="task-1",
        trace_id="f" * 32,
        parent_trace_id=req.trace_id,
        task_name="demo.tasks.compute",
        queue="celery",
    )
    storage.save_celery_task(task)

    saved = storage.get_request(req.id)
    assert saved is not None
    assert len(saved.celery_tasks) == 1
    assert saved.celery_tasks[0].task_id == "task-1"


def test_has_n_plus_one_respects_metadata_threshold():
    req = TraceRequest.create(method="GET", path="/demo", trace_id="a" * 32, span_id="b" * 16)
    req.metadata["n_plus_one_threshold"] = 3
    req.db_queries = [
        DBQuery.create(
            trace_id=req.trace_id,
            span_id=req.span_id,
            sql="SELECT 1",
            fingerprint="SELECT ?",
            duration_ms=1.0,
            parameters=[],
        )
        for _ in range(3)
    ]
    for query in req.db_queries:
        query.is_duplicate = True
        query.duplicate_count = 3

    assert req.has_n_plus_one is True
