"""FastAPI entrypoint: /health and the three /v1 endpoints (contract v1)."""

from __future__ import annotations

import logging
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from . import fingerprint, soundwave, storage
from .auth import require_secret
from .config import ConfigError, get_settings
from .errors import WorkerError, invalid_request
from .logging_config import configure_logging
from .schemas import FingerprintRequest, RenderWavecodeRequest, SoundwaveRequest

logger = logging.getLogger("personalization.api")
M = TypeVar("M", bound=BaseModel)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    get_settings()  # fail fast on invalid configuration
    yield


app = FastAPI(
    title="MEMORA personalization worker",
    version="1",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(WorkerError)
async def _worker_error(_: Request, exc: WorkerError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.body())


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unexpected error", extra={"ctx": {"error": type(exc).__name__}})
    code = "CONFIG_ERROR" if isinstance(exc, ConfigError) else "INTERNAL_ERROR"
    return JSONResponse(
        status_code=500,
        content={"status": "FAILED", "failureCode": code, "message": "Internal worker error."},
    )


async def _parse(request: Request, model: type[M]) -> M:
    body = await request.body()
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise invalid_request("Invalid request body: " + ", ".join(fields)) from exc


async def _handle(request: Request, model: type[M], fn: Callable[[M], Any]) -> JSONResponse:
    parsed = await _parse(request, model)
    result = await run_in_threadpool(fn, parsed)
    return JSONResponse(content=result.model_dump(mode="json"))


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        settings = get_settings()
        potrace_ok = shutil.which(settings.potrace_binary) is not None
        ffmpeg_ok = shutil.which(settings.ffmpeg_binary) is not None
        storage_ok = storage.check()
    except ConfigError:
        potrace_ok = ffmpeg_ok = storage_ok = False
    return {"status": "ok", "potrace": potrace_ok, "ffmpeg": ffmpeg_ok, "storage": storage_ok}


@app.post("/v1/fingerprint/process", dependencies=[Depends(require_secret)])
async def fingerprint_process(request: Request) -> JSONResponse:
    return await _handle(request, FingerprintRequest, fingerprint.run)


@app.post("/v1/soundwave/process", dependencies=[Depends(require_secret)])
async def soundwave_process(request: Request) -> JSONResponse:
    return await _handle(request, SoundwaveRequest, soundwave.run)


@app.post("/v1/soundwave/render-wavecode", dependencies=[Depends(require_secret)])
async def soundwave_render(request: Request) -> JSONResponse:
    return await _handle(request, RenderWavecodeRequest, soundwave.render)
