import asyncio
import json

import pytest


def _run(coro):
    return asyncio.run(coro)


async def _call_asgi(app, path: str, method: str = "GET", body: bytes = b"", headers=None):
    messages = []
    headers = headers or []
    sent_first = False

    async def receive():
        nonlocal sent_first
        if sent_first:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent_first = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "scheme": "http",
    }

    exc = None
    try:
        await app(scope, receive, send)
    except Exception as e:  # pragma: no cover - validated in assertions
        exc = e
    return messages, exc


def test_fastapi_middleware_persists_500_and_captures_bodies(tmp_path):
    pytest.importorskip("starlette")

    from tracegarden import TraceGardenConfig
    from tracegarden.core.storage import TraceStorage
    from tracegarden.integrations.fastapi.middleware import TraceGardenMiddleware

    async def demo_app(scope, receive, send):
        if scope["path"] == "/boom":
            raise RuntimeError("boom")

        body = b""
        while True:
            msg = await receive()
            if msg.get("type") != "http.request":
                continue
            body += msg.get("body", b"") or b""
            if not msg.get("more_body", False):
                break

        payload = json.loads(body.decode("utf-8") or "{}") if body else {}
        resp = json.dumps({"token": payload.get("token"), "ok": True}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": resp, "more_body": False})

    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    cfg = TraceGardenConfig(
        ui_token="secret",
        capture_request_body=True,
        capture_response_body=True,
    )
    middleware = TraceGardenMiddleware(demo_app, config=cfg, storage=storage)

    _, exc_ok = _run(
        _call_asgi(
            middleware,
            "/ok",
            method="POST",
            body=json.dumps({"password": "123", "token": "abc"}).encode("utf-8"),
            headers=[(b"content-type", b"application/json")],
        )
    )
    assert exc_ok is None

    _, exc_boom = _run(_call_asgi(middleware, "/boom", method="GET"))
    assert isinstance(exc_boom, RuntimeError)

    records = storage.list_requests(limit=10)
    by_path = {r.path: r for r in records}
    assert "/ok" in by_path
    assert "/boom" in by_path
    assert by_path["/boom"].status_code == 500
    assert "[REDACTED]" in by_path["/ok"].metadata.get("request_body", "")
    assert "[REDACTED]" in by_path["/ok"].metadata.get("response_body", "")
