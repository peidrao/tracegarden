import pytest

from tracegarden.integrations.http import install_http_instrumentation, uninstall_http_instrumentation


def test_install_and_uninstall_http_instrumentation_restores_originals():
    requests = pytest.importorskip("requests")
    httpx = pytest.importorskip("httpx")

    uninstall_http_instrumentation()
    original_requests = requests.Session.request
    original_httpx_sync = httpx.Client.request
    original_httpx_async = httpx.AsyncClient.request

    install_http_instrumentation()
    assert getattr(requests.Session.request, "_tracegarden_patched", False) is True
    assert getattr(httpx.Client.request, "_tracegarden_patched", False) is True
    assert getattr(httpx.AsyncClient.request, "_tracegarden_patched", False) is True

    uninstall_http_instrumentation()
    assert requests.Session.request is original_requests
    assert httpx.Client.request is original_httpx_sync
    assert httpx.AsyncClient.request is original_httpx_async
