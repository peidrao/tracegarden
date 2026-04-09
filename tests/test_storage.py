from datetime import datetime, timedelta, timezone

from tracegarden.core.models import TraceRequest
from tracegarden.core.storage import TraceStorage
from tracegarden.core.tracecontext import new_span_id, new_trace_id


def _make_request(path: str, started_at: datetime) -> TraceRequest:
    req = TraceRequest(
        id=new_trace_id(),
        trace_id=new_trace_id(),
        span_id=new_span_id(),
        method="GET",
        path=path,
        status_code=200,
        duration_ms=1.0,
        started_at=started_at,
        request_headers={},
        response_headers={},
    )
    return req


def test_save_request_prunes_oldest(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"), max_requests=2)
    t0 = datetime.now(timezone.utc)

    storage.save_request(_make_request("/a", t0))
    storage.save_request(_make_request("/b", t0 + timedelta(seconds=1)))
    storage.save_request(_make_request("/c", t0 + timedelta(seconds=2)))

    items = storage.list_requests(limit=10)
    assert storage.count_requests() == 2
    assert [i.path for i in items] == ["/c", "/b"]


def test_save_request_rolls_back_on_error(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"), max_requests=10)
    good = _make_request("/ok", datetime.now(timezone.utc))
    storage.save_request(good)

    broken = _make_request("/broken", datetime.now(timezone.utc))
    broken.method = None  # type: ignore[assignment]

    try:
        storage.save_request(broken)
        raised = False
    except Exception:
        raised = True

    assert raised is True
    assert storage.count_requests() == 1
    assert storage.list_requests(limit=1)[0].path == "/ok"


def test_save_request_never_exceeds_max_requests(tmp_path):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"), max_requests=3)
    t0 = datetime.now(timezone.utc)

    for i in range(20):
        storage.save_request(_make_request(f"/{i}", t0 + timedelta(seconds=i)))
        assert storage.count_requests() <= 3
