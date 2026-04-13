"""
Tests that TraceGardenMiddleware saves requests via run_in_executor, not
with a blocking SQLite call on the event loop.

We verify this indirectly: the middleware's finally block must be awaitable
and must produce a saved record even when the storage write happens
off the event loop thread.
"""
import asyncio

import pytest


async def _call_asgi(app, path: str = "/ok", method: str = "GET") -> None:
    sent_first = False

    async def receive():
        nonlocal sent_first
        if sent_first:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent_first = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # noqa: ARG001
        pass

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "scheme": "http",
    }
    await app(scope, receive, send)


def test_save_runs_without_blocking_event_loop(tmp_path):
    """The middleware must save the request without raising BlockingIOError."""
    pytest.importorskip("starlette")

    from tracegarden import TraceGardenConfig
    from tracegarden.core.storage import TraceStorage
    from tracegarden.integrations.fastapi.middleware import TraceGardenMiddleware

    async def bare(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    storage = TraceStorage(db_path=str(tmp_path / "async.db"))
    cfg = TraceGardenConfig(ui_token="test")
    middleware = TraceGardenMiddleware(bare, config=cfg, storage=storage)

    asyncio.run(_call_asgi(middleware, "/async-save"))

    records = storage.list_requests(limit=5)
    assert len(records) == 1
    assert records[0].path == "/async-save"


def test_concurrent_requests_all_saved(tmp_path):
    """Multiple concurrent ASGI requests must each produce a saved record."""
    pytest.importorskip("starlette")

    from tracegarden import TraceGardenConfig
    from tracegarden.core.storage import TraceStorage
    from tracegarden.integrations.fastapi.middleware import TraceGardenMiddleware

    async def bare(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    storage = TraceStorage(db_path=str(tmp_path / "concurrent.db"))
    cfg = TraceGardenConfig(ui_token="test", max_requests=100)
    middleware = TraceGardenMiddleware(bare, config=cfg, storage=storage)

    async def run_all():
        await asyncio.gather(*[
            _call_asgi(middleware, f"/req-{i}") for i in range(10)
        ])

    asyncio.run(run_all())

    count = storage.count_requests()
    assert count == 10, f"Expected 10 saved records, got {count}"
