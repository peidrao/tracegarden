"""
tracegarden.ui.routes
~~~~~~~~~~~~~~~~~~~~
Framework-agnostic UI route handlers plus per-framework mount helpers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from tracegarden import TraceGardenConfig
    from tracegarden.core.storage import TraceStorage

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _render(template_name: str, **context: object) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape  # type: ignore[import]

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        tmpl = env.get_template(template_name)
        return tmpl.render(**context)
    except ImportError:
        return _render_fallback(template_name, context)


def _render_fallback(template_name: str, context: dict) -> str:
    path = _TEMPLATES_DIR / template_name
    if not path.exists():
        return f"<html><body><p>Template {template_name!r} not found.</p></body></html>"
    tmpl_text = path.read_text()
    for key, value in context.items():
        tmpl_text = tmpl_text.replace("{{ " + key + " }}", str(value))
    return tmpl_text


def _check_auth(config: "TraceGardenConfig", request_token: Optional[str]) -> bool:
    if config.ui_token is None:
        return True
    return request_token == config.ui_token


def _extract_token(
    query_string: str,
    cookies: dict,
    headers: Optional[dict],
    token_header: str,
) -> Optional[str]:
    from urllib.parse import parse_qs

    normalized_headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    header_key = token_header.lower()
    if header_key in normalized_headers:
        return str(normalized_headers[header_key])

    qs = parse_qs(query_string or "")
    if "token" in qs:
        return qs["token"][0]
    return cookies.get("tg_token")


def _query_groups(req, threshold: int) -> list:
    grouped = {}
    for q in req.db_queries:
        bucket = grouped.setdefault(
            q.fingerprint,
            {
                "fingerprint": q.fingerprint,
                "count": 0,
                "total_ms": 0.0,
                "max_ms": 0.0,
                "example_sql": q.sql,
            },
        )
        bucket["count"] += 1
        bucket["total_ms"] += q.duration_ms
        bucket["max_ms"] = max(bucket["max_ms"], q.duration_ms)

    groups = list(grouped.values())
    for g in groups:
        g["is_n_plus_one"] = g["count"] >= threshold
    groups.sort(key=lambda row: (row["count"], row["total_ms"]), reverse=True)
    return groups


def handle_index(
    storage: "TraceStorage",
    config: "TraceGardenConfig",
    page: int = 1,
    token: Optional[str] = None,
) -> tuple[int, str, str]:
    if not _check_auth(config, token):
        return 401, "text/html; charset=utf-8", "<p>Unauthorized</p>"

    per_page = 50
    offset = (page - 1) * per_page
    requests = storage.list_requests(limit=per_page, offset=offset)
    total = storage.count_requests()
    total_pages = max(1, (total + per_page - 1) // per_page)

    body = _render(
        "index.html",
        requests=requests,
        page=page,
        total_pages=total_pages,
        total=total,
        config=config,
    )
    return 200, "text/html; charset=utf-8", body


def handle_detail(
    request_id: str,
    storage: "TraceStorage",
    config: "TraceGardenConfig",
    token: Optional[str] = None,
) -> tuple[int, str, str]:
    if not _check_auth(config, token):
        return 401, "text/html", "<p>Unauthorized</p>"

    req = storage.get_request(request_id)
    if req is None:
        return 404, "text/html", "<p>Request not found.</p>"

    from tracegarden.core.fingerprint import detect_n_plus_one

    n_plus_one_warnings = detect_n_plus_one(
        req.db_queries, threshold=config.n_plus_one_threshold
    )

    body = _render(
        "detail.html",
        req=req,
        query_groups=_query_groups(req, config.n_plus_one_threshold),
        n_plus_one_warnings=n_plus_one_warnings,
        config=config,
    )
    return 200, "text/html; charset=utf-8", body


def handle_export(
    request_id: str,
    storage: "TraceStorage",
    config: "TraceGardenConfig",
    token: Optional[str] = None,
) -> tuple[int, str, str]:
    if not _check_auth(config, token):
        return 401, "application/json", json.dumps({"error": "Unauthorized"})

    req = storage.get_request(request_id)
    if req is None:
        return 404, "application/json", json.dumps({"error": "Not found"})

    export_data = {
        "tracegarden_version": __import__("tracegarden").__version__,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "ui_prefix": config.ui_prefix,
            "n_plus_one_threshold": config.n_plus_one_threshold,
        },
        "request": req.to_dict(),
    }
    return 200, "application/json", json.dumps(export_data, indent=2)


def handle_static(filename: str) -> tuple[int, str, bytes]:
    safe_name = os.path.basename(filename)
    path = _STATIC_DIR / safe_name
    if not path.exists():
        return 404, "text/plain", b"Not found"
    content_type = "text/css" if safe_name.endswith(".css") else "application/octet-stream"
    return 200, content_type, path.read_bytes()


def mount_django_urls(config=None, storage=None):
    from django.http import HttpResponse  # type: ignore[import]
    from django.urls import path  # type: ignore[import]

    def _get_config():
        if config is not None:
            return config
        from django.conf import settings  # type: ignore[import]
        from tracegarden import TraceGardenConfig

        tg = getattr(settings, "TRACEGARDEN", {})
        return TraceGardenConfig(
            **{k: v for k, v in tg.items() if k in TraceGardenConfig.__dataclass_fields__}
        )

    def _get_storage():
        if storage is not None:
            return storage
        from tracegarden.core.storage import get_default_storage

        return get_default_storage()

    def _token_from_request(request):
        cfg = _get_config()
        return _extract_token(
            request.META.get("QUERY_STRING", ""),
            request.COOKIES,
            request.headers,
            cfg.ui_token_header,
        )

    def view_index(request):
        page = int(request.GET.get("page", 1))
        status, ct, body = handle_index(
            _get_storage(), _get_config(), page=page, token=_token_from_request(request)
        )
        return HttpResponse(body, content_type=ct, status=status)

    def view_detail(request, request_id):
        status, ct, body = handle_detail(
            request_id,
            _get_storage(),
            _get_config(),
            token=_token_from_request(request),
        )
        return HttpResponse(body, content_type=ct, status=status)

    def view_export(request, request_id):
        status, ct, body = handle_export(
            request_id,
            _get_storage(),
            _get_config(),
            token=_token_from_request(request),
        )
        return HttpResponse(body, content_type=ct, status=status)

    def view_static(request, filename):
        status, ct, body = handle_static(filename)
        return HttpResponse(body, content_type=ct, status=status)

    cfg = _get_config()
    prefix = cfg.ui_prefix.strip("/")
    return [
        path(f"{prefix}/", view_index, name="tracegarden_index"),
        path(f"{prefix}/request/<str:request_id>/", view_detail, name="tracegarden_detail"),
        path(f"{prefix}/export/<str:request_id>/", view_export, name="tracegarden_export"),
        path(f"{prefix}/static/<str:filename>", view_static, name="tracegarden_static"),
    ]


def mount_flask_blueprint(app, config=None, storage=None):
    from flask import Blueprint, Response, request as flask_request  # type: ignore[import]

    def _get_config():
        if config is not None:
            return config
        from tracegarden import TraceGardenConfig

        return TraceGardenConfig()

    cfg = _get_config()
    bp = Blueprint("tracegarden", __name__, url_prefix=cfg.ui_prefix)

    def _get_storage():
        if storage is not None:
            return storage
        from tracegarden.core.storage import get_default_storage

        return get_default_storage()

    def _token():
        return _extract_token(
            flask_request.query_string.decode(),
            flask_request.cookies,
            flask_request.headers,
            cfg.ui_token_header,
        )

    @bp.route("/")
    def index():
        page = int(flask_request.args.get("page", 1))
        status, ct, body = handle_index(_get_storage(), cfg, page=page, token=_token())
        return Response(body, status=status, content_type=ct)

    @bp.route("/request/<request_id>/")
    def detail(request_id):
        status, ct, body = handle_detail(request_id, _get_storage(), cfg, token=_token())
        return Response(body, status=status, content_type=ct)

    @bp.route("/export/<request_id>/")
    def export(request_id):
        status, ct, body = handle_export(request_id, _get_storage(), cfg, token=_token())
        return Response(body, status=status, content_type=ct)

    @bp.route("/static/<filename>")
    def static_file(filename):
        status, ct, body = handle_static(filename)
        return Response(body, status=status, content_type=ct)

    app.register_blueprint(bp)


def mount_fastapi_router(app, config=None, storage=None):
    from fastapi import APIRouter  # type: ignore[import]
    from starlette.requests import Request  # type: ignore[import]
    from starlette.responses import Response  # type: ignore[import]

    def _get_config():
        if config is not None:
            return config
        from tracegarden import TraceGardenConfig

        return TraceGardenConfig()

    cfg = _get_config()
    router = APIRouter(prefix=cfg.ui_prefix, tags=["tracegarden"])

    def _get_storage():
        if storage is not None:
            return storage
        from tracegarden.core.storage import get_default_storage

        return get_default_storage()

    def _token(request: Request) -> Optional[str]:
        return _extract_token(
            str(request.query_params),
            dict(request.cookies),
            request.headers,
            cfg.ui_token_header,
        )

    @router.get("/")
    async def ui_index(request: Request, page: int = 1):
        status, ct, body = handle_index(
            _get_storage(), cfg, page=page, token=_token(request)
        )
        return Response(content=body, status_code=status, media_type=ct)

    @router.get("/request/{request_id}/")
    async def ui_detail(request_id: str, request: Request):
        status, ct, body = handle_detail(
            request_id, _get_storage(), cfg, token=_token(request)
        )
        return Response(content=body, status_code=status, media_type=ct)

    @router.get("/export/{request_id}/")
    async def ui_export(request_id: str, request: Request):
        status, ct, body = handle_export(
            request_id, _get_storage(), cfg, token=_token(request)
        )
        return Response(content=body, status_code=status, media_type=ct)

    @router.get("/static/{filename}")
    async def ui_static(filename: str):
        status, ct, body = handle_static(filename)
        return Response(content=body, status_code=status, media_type=ct)

    app.include_router(router)
