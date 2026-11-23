"""Request-scoped helpers: X-Request-Id handling and the secret-before-body check."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request

from ..core.errors import WorkerError
from .dependencies import require_secret

REQUEST_ID_HEADER = "X-Request-Id"
SECRET_HEADER = "x-memora-worker-secret"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def request_id(request: Request) -> str | None:
    """Valid `X-Request-Id` (max 100 chars, [A-Za-z0-9_.:-]) or None."""
    raw = request.headers.get(REQUEST_ID_HEADER)
    return raw if raw is not None and _REQUEST_ID.fullmatch(raw) else None


def response_headers(request: Request, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    rid = request_id(request)
    if rid is not None:
        headers[REQUEST_ID_HEADER] = rid
    return headers


def log_ctx(request: Request, **fields: Any) -> dict[str, Any]:
    """`extra=` payload for the JSON logger; carries the request id, never the body."""
    ctx: dict[str, Any] = {"path": request.url.path, **fields}
    rid = request_id(request)
    if rid is not None:
        ctx["requestId"] = rid
    return {"ctx": ctx}


def check_secret_first(request: Request) -> WorkerError | None:
    """FastAPI parses the JSON body before running dependencies, so a malformed body could
    otherwise answer 400 to a caller with a wrong secret. Auth always wins on `/v1/`."""
    if not request.url.path.startswith("/v1/"):
        return None
    try:
        require_secret(request.headers.get(SECRET_HEADER))
    except WorkerError as exc:
        return exc
    return None
