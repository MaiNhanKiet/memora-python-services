"""GET /v1/fingerprint/presets, POST /v1/fingerprint/process, /render-crop and /select-version."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ...processing import fingerprint_design as design
from ...schemas import (
    Envelope,
    FingerprintPresetsResponse,
    FingerprintRequest,
    FingerprintResponse,
    RenderCropRequest,
    RenderCropResponse,
    SelectVersionRequest,
    SelectVersionResponse,
)
from ..dependencies import require_secret
from ..responses import error_responses, ok_response, run_and_respond

router = APIRouter(tags=["Fingerprint"], dependencies=[Depends(require_secret)])

_BASE_KEY = "personalization/fingerprint/{orderId}/selected/{sourceId}/"


@router.get(
    "/v1/fingerprint/presets",
    summary="Danh sách khung vân tay",
    description="Các khung `shape` (NATURAL, CIRCLE, HEART, SQUARE) kèm tên, mô tả và SVG mẫu "
    "(biểu tượng đường viền, không chứa dữ liệu sinh trắc).",
    responses={
        200: {"model": Envelope[FingerprintPresetsResponse], "description": "Danh sách khung."},
        **{
            k: v
            for k, v in error_responses(
                "/v1/fingerprint/presets", "", ("INTERNAL_ERROR", "Internal worker error.")
            ).items()
            if k in (401, 500)
        },
    },
)
async def fingerprint_presets(request: Request) -> JSONResponse:
    result = await run_in_threadpool(design.presets)
    return ok_response(request, "Fingerprint presets", result.model_dump(mode="json"))


@router.post(
    "/v1/fingerprint/process",
    summary="Xử lý ảnh vân tay",
    description="Đọc `rawKey` (BMP AS608) từ storage, làm sạch (bỏ đốm đặc, mảng dính viền), "
    "ghi `base.png` (~2048 px), phiên bản mặc định `v0` (khung NATURAL) tại "
    "`versions/v0/fingerprint.svg` và `fingerprint_alpha.png`, cùng `manifest.json` dưới "
    "`outputPrefix`.",
    responses={
        200: {"model": Envelope[FingerprintResponse], "description": "Xử lý xong (READY)."},
        **error_responses(
            "/v1/fingerprint/process",
            "Object not found: personalization/fingerprint/raw/s1/a1",
            ("IMAGE_UNREADABLE", "The raw object is not a readable image."),
        ),
    },
)
async def fingerprint_process(request: Request, body: FingerprintRequest) -> JSONResponse:
    return await run_and_respond(request, body, design.process, "Fingerprint processed")


@router.post(
    "/v1/fingerprint/render-crop",
    summary="Cắt khung và tạo phiên bản vân tay",
    description="Đọc `base.png`, xóa các nét tẩy `eraseStrokes` (tối đa 15 % ngấn vân), đặt khung "
    "`shape` lệch `offsetX/offsetY` với `zoom`, vector hóa và ghi "
    "`versions/{versionId}/fingerprint.svg` + `fingerprint_alpha.png`. Cùng `versionId` thì chỉ "
    "ghi đè phiên bản đó.",
    responses={
        200: {"model": Envelope[RenderCropResponse], "description": "Đã tạo phiên bản (READY)."},
        **error_responses(
            "/v1/fingerprint/render-crop",
            f"Object not found: {_BASE_KEY}base.png",
            (
                "FRAME_COVERAGE_TOO_LOW",
                "The frame contains 52% ridges; at least 70% is required.",
            ),
        ),
    },
)
async def fingerprint_render_crop(request: Request, body: RenderCropRequest) -> JSONResponse:
    return await run_and_respond(request, body, design.render_crop, "Fingerprint crop rendered")


@router.post(
    "/v1/fingerprint/select-version",
    summary="Chấp nhận một phiên bản vân tay",
    description="Giữ phiên bản `versionId`, xóa các phiên bản khác và `base.png` (bản vector "
    "được chấp nhận là bản cuối), ghi lại `manifest.json` với `accepted: true`. Gọi lại là "
    "no-op. Phiên bản không có thì `422 VERSION_NOT_FOUND`.",
    responses={
        200: {"model": Envelope[SelectVersionResponse], "description": "Đã chấp nhận (READY)."},
        **error_responses(
            "/v1/fingerprint/select-version",
            f"Object not found: {_BASE_KEY}manifest.json",
            ("VERSION_NOT_FOUND", "Artwork version v3 does not exist."),
        ),
    },
)
async def fingerprint_select_version(request: Request, body: SelectVersionRequest) -> JSONResponse:
    return await run_and_respond(
        request, body, design.select_version, "Fingerprint version selected"
    )
