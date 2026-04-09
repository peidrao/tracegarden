from tracegarden.core.redaction import Redactor


def test_headers_redaction_default_and_allowlist():
    redactor = Redactor(header_allowlist={"authorization"})
    headers = {
        "authorization": "Bearer secret",
        "cookie": "a=b",
        "x-api-key": "abc",
        "x-custom": "ok",
    }
    out = redactor.redact_headers(headers)
    assert out["authorization"] == "Bearer secret"
    assert out["cookie"] == "[REDACTED]"
    assert out["x-api-key"] == "[REDACTED]"
    assert out["x-custom"] == "ok"


def test_url_query_redaction():
    redactor = Redactor()
    url = "https://example.com/search?q=books&token=abc123&page=2"
    out = redactor.redact_url_params(url)
    assert "token=%5BREDACTED%5D" in out
    assert "q=books" in out
    assert "page=2" in out


def test_json_body_recursive_redaction():
    redactor = Redactor(param_denylist={"ssn"})
    body = '{"user":{"password":"123","profile":{"ssn":"111"}},"items":[{"token":"x"}]}'
    out = redactor.redact_body(body, "application/json")
    assert '"password": "[REDACTED]"' in out
    assert '"ssn": "[REDACTED]"' in out
    assert '"token": "[REDACTED]"' in out


def test_redact_params_list_of_dicts():
    redactor = Redactor()
    params = {"items": [{"token": "abc", "safe": "ok"}]}
    out = redactor.redact_params(params)
    assert out["items"][0]["token"] == "[REDACTED]"
    assert out["items"][0]["safe"] == "ok"


def test_redact_db_params_handles_dict_and_nested_values():
    redactor = Redactor()
    params = {"password": "123", "filters": [{"token": "abc"}], "page": 1}
    out = redactor.redact_db_params(params)
    assert out["password"] == "[REDACTED]"
    assert out["filters"][0]["token"] == "[REDACTED]"
    assert out["page"] == 1


def test_non_json_body_is_fully_redacted():
    redactor = Redactor()
    body = "credit_card=4111111111111111"
    out = redactor.redact_body(body, "text/plain")
    assert out == "[BODY REDACTED]"
