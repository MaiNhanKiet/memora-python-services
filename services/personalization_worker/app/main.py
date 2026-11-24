"""FastAPI entrypoint (`uvicorn app.main:app`): wires routers, handlers and the docs toggle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.exception_handlers import register_exception_handlers
from .api.routes import fingerprint, health, soundwave
from .core.config import ConfigError, get_settings
from .core.logging import configure_logging
from .core.version import PROCESSOR_VERSION

DESCRIPTION = (
    "Worker xử lý vân tay / soundwave cho MEMORA Personalization. "
    "Mọi phản hồi dùng chung envelope với API Gateway: thành công "
    "`{statusCode, message, data}`, lỗi `{statusCode, code, message, path, timestamp}`. "
    "Các endpoint `/v1` cần header `X-Memora-Worker-Secret` (nút Authorize)."
)
TAGS = [
    {"name": "Health", "description": "Kiểm tra sống và công cụ (không cần secret)."},
    {"name": "Fingerprint", "description": "Ảnh vân tay sang SVG + PNG để khắc laser."},
    {"name": "Soundwave", "description": "Audio sang WAVECODE (SVG + PNG)."},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    get_settings()  # fail fast on invalid configuration
    yield


def _docs_enabled() -> bool:
    try:
        return get_settings().docs_enabled
    except ConfigError:
        return False


def create_app() -> FastAPI:
    application = FastAPI(
        title="MEMORA personalization worker",
        version=PROCESSOR_VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    register_exception_handlers(application)

    # Docs are read from settings per request, so WORKER_DOCS_ENABLED=false answers 404.
    @application.get("/openapi.json", include_in_schema=False)
    async def openapi_schema() -> JSONResponse:
        if not _docs_enabled():
            raise StarletteHTTPException(404, "Not Found")
        return JSONResponse(application.openapi())

    @application.get("/docs", include_in_schema=False)
    async def swagger_ui() -> Any:
        if not _docs_enabled():
            raise StarletteHTTPException(404, "Not Found")
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{application.title} - Swagger UI",
            swagger_ui_parameters={"persistAuthorization": True},
        )

    application.include_router(health.router)
    application.include_router(fingerprint.router)
    application.include_router(soundwave.router)
    return application


app = create_app()
