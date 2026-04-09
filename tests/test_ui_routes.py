import json

import pytest

from tracegarden import TraceGardenConfig
from tracegarden.core.models import TraceRequest
from tracegarden.core.storage import TraceStorage
from tracegarden.ui import routes


def _seed_request(storage: TraceStorage) -> TraceRequest:
    req = TraceRequest.create(method="GET", path="/demo")
    req.status_code = 200
    req.duration_ms = 12.5
    storage.save_request(req)
    return req


def test_handle_index_requires_token(tmp_path):
    pytest.importorskip("jinja2")
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    _seed_request(storage)
    cfg = TraceGardenConfig(ui_token="secret")

    status, _, _ = routes.handle_index(storage, cfg, token="wrong")
    assert status == 401

    status_ok, _, body_ok = routes.handle_index(storage, cfg, token="secret")
    assert status_ok == 200
    assert "TraceGarden" in body_ok


def test_handle_static_js_mime_type(tmp_path, monkeypatch):
    js_file = tmp_path / "app.js"
    js_file.write_text("console.log('ok');", encoding="utf-8")
    monkeypatch.setattr(routes, "_STATIC_DIR", tmp_path)

    status, ct, body = routes.handle_static("app.js")
    assert status == 200
    assert ct.startswith("application/javascript")
    assert body == b"console.log('ok');"


def test_handle_detail_and_export(tmp_path):
    pytest.importorskip("jinja2")
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    req = _seed_request(storage)
    cfg = TraceGardenConfig(ui_token="secret")

    status_404, _, _ = routes.handle_detail("missing", storage, cfg, token="secret")
    assert status_404 == 404

    status_detail, _, body_detail = routes.handle_detail(req.id, storage, cfg, token="secret")
    assert status_detail == 200
    assert req.path in body_detail

    status_export, ct_export, body_export = routes.handle_export(req.id, storage, cfg, token="secret")
    payload = json.loads(body_export)
    assert status_export == 200
    assert ct_export == "application/json"
    assert payload["request"]["id"] == req.id
