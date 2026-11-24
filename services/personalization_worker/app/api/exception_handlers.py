"""Every error leaves the worker as the Gateway error envelope."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core.config import ConfigError
from ..core.errors import WorkerError
from .middleware import check_secret_first, log_ctx
from .responses import error_response

logger = logging.getLogger("personalization.api")
_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


async def worker_error(request: Request, exc: WorkerError) -> JSONResponse:
    logger.warning(
        "request rejected", extra=log_ctx(request, status=exc.http_status, code=exc.failure_code)
    )
    return error_response(request, exc.http_status, exc.failure_code, exc.message)


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    auth_failure = check_secret_first(request)
    if auth_failure is not None:
        return await worker_error(request, auth_failure)
    fields = sorted(
        {".".join(str(p) for p in e["loc"] if p != "body") or "body" for e in exc.errors()}
    )
    logger.warning("request rejected", extra=log_ctx(request, status=400, code="INVALID_REQUEST"))
    return error_response(
        request, 400, "INVALID_REQUEST", "Invalid request body: " + ", ".join(fields)
    )


async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else code
    extra = {k: v for k, v in (exc.headers or {}).items() if k.lower() == "allow"}
    return error_response(request, exc.status_code, code, message, extra)


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unexpected error", extra=log_ctx(request, error=type(exc).__name__))
    code = "CONFIG_ERROR" if isinstance(exc, ConfigError) else "INTERNAL_ERROR"
    return error_response(request, 500, code, "Internal worker error.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(WorkerError, worker_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error)
