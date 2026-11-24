"""GET /health (public)."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...core.config import ConfigError, get_settings
from ...schemas import Envelope, HealthData
from ...storage import s3 as storage
from ..responses import ok_response

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="Kiểm tra sống",
    description="Trả `data.status = UP` cùng cờ `potrace`, `ffmpeg`, `storage`. Không cần secret.",
    response_model=Envelope[HealthData],
)
def health(request: Request) -> JSONResponse:
    try:
        settings = get_settings()
        potrace_ok = shutil.which(settings.potrace_binary) is not None
        ffmpeg_ok = shutil.which(settings.ffmpeg_binary) is not None
        storage_ok = storage.check()
    except ConfigError:
        potrace_ok = ffmpeg_ok = storage_ok = False
    data = {"status": "UP", "potrace": potrace_ok, "ffmpeg": ffmpeg_ok, "storage": storage_ok}
    return ok_response(request, "OK", data)
