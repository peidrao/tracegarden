"""
tracegarden.core.redaction
~~~~~~~~~~~~~~~~~~~~~~~~~~
Header, parameter, and request body redaction.
"""
from __future__ import annotations

import json
import re
from typing import Optional, Set
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

SENSITIVE_HEADERS: Set[str] = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-secret",
    "x-access-token",
    "proxy-authorization",
}

SENSITIVE_PARAMS: Set[str] = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "access_token",
    "refresh_token",
    "private_key",
    "client_secret",
    "auth_token",
    "session_key",
    "passphrase",
}

_DEFAULT_REDACTED = "[REDACTED]"


class Redactor:
    """
    Applies configurable redaction to headers, query parameters, and request bodies.

    Parameters
    ----------
    header_denylist:
        Additional header names (lowercase) to redact on top of SENSITIVE_HEADERS.
    param_denylist:
        Additional parameter names (lowercase) to redact on top of SENSITIVE_PARAMS.
    header_allowlist:
        Header names that are exempted from redaction even if on the denylist.
        Useful in fully-trusted local environments.
    redact_value:
        The string used as the replacement for redacted values.
    """

    def __init__(
        self,
        header_denylist: Optional[set] = None,
        param_denylist: Optional[set] = None,
        header_allowlist: Optional[set] = None,
        redact_value: str = _DEFAULT_REDACTED,
    ):
        self._header_deny: Set[str] = SENSITIVE_HEADERS | {
            h.lower() for h in (header_denylist or set())
        }
        self._param_deny: Set[str] = SENSITIVE_PARAMS | {
            p.lower() for p in (param_denylist or set())
        }
        self._header_allow: Set[str] = {
            h.lower() for h in (header_allowlist or set())
        }
        self.redact_value = redact_value

    def _is_header_sensitive(self, name: str) -> bool:
        lower = name.lower()
        if lower in self._header_allow:
            return False
        return lower in self._header_deny

    def _is_param_sensitive(self, name: str) -> bool:
        return name.lower() in self._param_deny

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def redact_headers(self, headers: dict) -> dict:
        """Return a copy of *headers* with sensitive values replaced."""
        if not headers:
            return {}
        return {
            k: (self.redact_value if self._is_header_sensitive(k) else v)
            for k, v in headers.items()
        }

    def redact_params(self, params: dict) -> dict:
        """Return a copy of *params* with sensitive values replaced."""
        if not params:
            return {}
        result = {}
        for k, v in params.items():
            if self._is_param_sensitive(k):
                result[k] = self.redact_value
            elif isinstance(v, dict):
                result[k] = self.redact_params(v)
            elif isinstance(v, list):
                result[k] = [
                    self.redact_value if self._is_param_sensitive(k) else item
                    for item in v
                ]
            else:
                result[k] = v
        return result

    def redact_body(self, body: str, content_type: str = "") -> str:
        """
        Redact a request or response body.

        - application/json: recursively redact sensitive keys in the parsed object.
        - application/x-www-form-urlencoded: redact sensitive keys.
        - Everything else: return body unchanged (binary / unknown content is not parsed).
        """
        if not body:
            return body
        ct = (content_type or "").lower().split(";")[0].strip()
        if ct == "application/json":
            return self._redact_json_body(body)
        if ct == "application/x-www-form-urlencoded":
            return self._redact_form_body(body)
        return body

    def redact_url_params(self, url: str) -> str:
        """Redact sensitive query parameters in a URL string."""
        if not url or "?" not in url:
            return url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        redacted_qs: dict = {}
        for key, values in qs.items():
            if self._is_param_sensitive(key):
                redacted_qs[key] = [self.redact_value]
            else:
                redacted_qs[key] = values
        new_query = urlencode(redacted_qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _redact_json_body(self, body: str) -> str:
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body
        redacted_obj = self._redact_dict_recursive(obj)
        return json.dumps(redacted_obj)

    def _redact_dict_recursive(self, obj: object) -> object:
        if isinstance(obj, dict):
            return {
                k: (self.redact_value if self._is_param_sensitive(k) else self._redact_dict_recursive(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [self._redact_dict_recursive(item) for item in obj]
        return obj

    def _redact_form_body(self, body: str) -> str:
        try:
            params = parse_qs(body, keep_blank_values=True)
        except Exception:
            return body
        redacted: dict = {}
        for key, values in params.items():
            if self._is_param_sensitive(key):
                redacted[key] = [self.redact_value]
            else:
                redacted[key] = values
        return urlencode(redacted, doseq=True)


# Module-level default redactor (no allowlists — strict mode).
_default_redactor: Optional[Redactor] = None


def get_default_redactor() -> Redactor:
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = Redactor()
    return _default_redactor


def configure_redactor(
    header_denylist: Optional[set] = None,
    param_denylist: Optional[set] = None,
    header_allowlist: Optional[set] = None,
    redact_value: str = _DEFAULT_REDACTED,
) -> Redactor:
    """Create and install a new global redactor with the given settings."""
    global _default_redactor
    _default_redactor = Redactor(
        header_denylist=header_denylist,
        param_denylist=param_denylist,
        header_allowlist=header_allowlist,
        redact_value=redact_value,
    )
    return _default_redactor
