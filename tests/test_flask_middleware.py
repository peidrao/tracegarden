import pytest


def test_flask_middleware_captures_bodies_and_redacts(tmp_path):
    flask = pytest.importorskip("flask")
    from tracegarden import TraceGarden, TraceGardenConfig

    app = flask.Flask(__name__)
    app.testing = True

    @app.route("/echo", methods=["POST"])
    def echo():
        payload = flask.request.get_json(force=True)
        return flask.jsonify({"token": payload.get("token"), "ok": True})

    cfg = TraceGardenConfig(
        db_path=str(tmp_path / "tg.db"),
        ui_token="secret",
        capture_request_body=True,
        capture_response_body=True,
    )
    tg = TraceGarden(app, config=cfg)

    client = app.test_client()
    resp = client.post("/echo", json={"password": "123", "token": "abc"})
    assert resp.status_code == 200

    saved = tg.storage.list_requests(limit=1)[0]
    assert saved.path == "/echo"
    assert "[REDACTED]" in saved.metadata.get("request_body", "")
    assert "[REDACTED]" in saved.metadata.get("response_body", "")
