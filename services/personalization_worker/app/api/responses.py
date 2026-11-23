"""Gateway-style envelopes and the OpenAPI `responses` documentation per endpoint."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..schemas import ErrorEnvelope
from .middleware import log_ctx, response_headers

logger = logging.getLogger("personalization.api")


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "statusCode": status,
            "code": code,
            "message": message,
            "path": request.url.path,
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        },
        headers=response_headers(request, extra_headers),
    )


def ok_response(request: Request, message: str, data: Any) -> JSONResponse:
    return JSONResponse(
        content={"statusCode": 200, "message": message, "data": data},
        headers=response_headers(request),
    )


async def run_and_respond(
    request: Request, body: Any, fn: Callable[[Any], Any], message: str
) -> JSONResponse:
    """Run a processing function off the event loop and wrap its result in the envelope."""
    result = await run_in_threadpool(fn, body)
    logger.info("request processed", extra=log_ctx(request, sourceId=body.sourceId))
    return ok_response(request, message, result.model_dump(mode="json"))


_EXAMPLE_TS = "2026-11-23T08:15:30.123Z"


def _err_doc(status: int, code: str, message: str, path: str, description: str) -> dict[str, Any]:
    example = {
        "statusCode": status,
        "code": code,
        "message": message,
        "path": path,
        "timestamp": _EXAMPLE_TS,
    }
    return {
        "model": ErrorEnvelope,
        "description": description,
        "content": {"application/json": {"example": example}},
    }


def error_responses(
    path: str, not_found: str, unprocessable: tuple[str, str]
) -> dict[int | str, dict[str, Any]]:
    return {
        400: _err_doc(
            400,
            "INVALID_REQUEST",
            "Invalid request body: outputPrefix, rawKey",
            path,
            "Body không hợp lệ (INVALID_REQUEST).",
        ),
        401: _err_doc(
            401,
            "UNAUTHORIZED",
            "Invalid or missing worker secret.",
            path,
            "Thiếu hoặc sai secret (UNAUTHORIZED).",
        ),
        404: _err_doc(404, "OBJECT_NOT_FOUND", not_found, path, "Không thấy object trong storage."),
        422: _err_doc(
            422,
            unprocessable[0],
            unprocessable[1],
            path,
            "Dữ liệu không xử lý được (xem `code`: IMAGE_UNREADABLE, "
            "FINGERPRINT_REGION_NOT_FOUND, AUDIO_UNREADABLE, AUDIO_DURATION_INVALID, "
            "SEGMENT_OUT_OF_RANGE).",
        ),
        500: _err_doc(
            500,
            "INTERNAL_ERROR",
            "Internal worker error.",
            path,
            "Lỗi nội bộ (INTERNAL_ERROR / CONFIG_ERROR), không lộ chi tiết.",
        ),
        503: _err_doc(
            503,
            "STORAGE_UNAVAILABLE",
            "Object storage is unavailable.",
            path,
            "Tạm thời không dùng được (STORAGE_UNAVAILABLE / TOOL_UNAVAILABLE).",
        ),
    }
