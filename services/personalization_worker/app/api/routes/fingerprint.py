"""POST /v1/fingerprint/process."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...processing import fingerprint as pipeline
from ...schemas import Envelope, FingerprintRequest, FingerprintResponse
from ..dependencies import require_secret
from ..responses import error_responses, run_and_respond

router = APIRouter(tags=["Fingerprint"], dependencies=[Depends(require_secret)])


@router.post(
    "/v1/fingerprint/process",
    summary="Xử lý ảnh vân tay",
    description="Đọc `rawKey` (BMP AS608) từ storage, tạo `fingerprint.svg`, "
    "`fingerprint_alpha.png`, `manifest.json` dưới `outputPrefix`.",
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
    return await run_and_respond(request, body, pipeline.run, "Fingerprint processed")
