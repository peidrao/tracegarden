import json

import pytest


def _setup_django():
    django = pytest.importorskip("django")
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            SECRET_KEY="tracegarden-test",
            ROOT_URLCONF=__name__,
            ALLOWED_HOSTS=["*"],
            MIDDLEWARE=[],
            INSTALLED_APPS=[],
            TRACEGARDEN={
                "enabled": True,
                "ui_prefix": "/__tracegarden",
                "capture_request_body": True,
                "capture_response_body": True,
            },
        )
        django.setup()


def test_django_middleware_persists_500_and_captures_request_body(tmp_path):
    _setup_django()

    from django.conf import settings
    from django.http import HttpResponse
    from django.test import RequestFactory

    from tracegarden.integrations.django.middleware import TraceGardenMiddleware

    settings.TRACEGARDEN["db_path"] = str(tmp_path / "tg.db")

    rf = RequestFactory()

    def ok_response(_request):
        return HttpResponse(
            json.dumps({"token": "abc"}),
            content_type="application/json",
            status=200,
        )

    ok_middleware = TraceGardenMiddleware(ok_response)
    ok_req = rf.post(
        "/ok?token=abc",
        data=json.dumps({"password": "123"}),
        content_type="application/json",
    )
    resp = ok_middleware(ok_req)
    assert resp.status_code == 200

    def boom_response(_request):
        raise RuntimeError("boom")

    boom_middleware = TraceGardenMiddleware(boom_response)
    boom_req = rf.get("/boom")
    with pytest.raises(RuntimeError):
        boom_middleware(boom_req)

    storage = ok_middleware._storage
    records = storage.list_requests(limit=10)
    by_path = {r.path: r for r in records}
    assert "/ok" in by_path
    assert "/boom" in by_path
    assert by_path["/boom"].status_code == 500
    assert "[REDACTED]" in by_path["/ok"].metadata.get("request_body", "")
    assert "[REDACTED]" in by_path["/ok"].metadata.get("response_body", "")
