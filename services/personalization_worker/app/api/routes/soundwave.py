"""POST /v1/soundwave/process and /v1/soundwave/render-wavecode."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...processing import soundwave as pipeline
from ...schemas import (
    Envelope,
    RenderResponse,
    RenderWavecodeRequest,
    SoundwaveRequest,
    SoundwaveResponse,
)
from ..dependencies import require_secret
from ..responses import error_responses, run_and_respond

router = APIRouter(tags=["Soundwave"], dependencies=[Depends(require_secret)])


@router.post(
    "/v1/soundwave/process",
    summary="Xử lý audio soundwave",
    description="Giải mã audio, cắt đoạn 3 s tại `segmentStartMs`, tạo WAVECODE "
    "(`wavecode.svg`, `wavecode_alpha.png`, `audio_segment.wav`, `manifest.json`).",
    responses={
        200: {"model": Envelope[SoundwaveResponse], "description": "Xử lý xong (READY)."},
        **error_responses(
            "/v1/soundwave/process",
            "Object not found: personalization/sources/{sourceId}/raw",
            ("AUDIO_DURATION_INVALID", "Measured duration 2000 ms is outside 3000..30000 ms."),
        ),
    },
)
async def soundwave_process(request: Request, body: SoundwaveRequest) -> JSONResponse:
    return await run_and_respond(request, body, pipeline.run, "Soundwave processed")


@router.post(
    "/v1/soundwave/render-wavecode",
    summary="Vẽ lại WAVECODE từ 18 level",
    description="Render lại WAVECODE từ danh sách 18 level (0..7), dùng cho bước nudge duy nhất.",
    responses={
        200: {"model": Envelope[RenderResponse], "description": "Render xong (READY)."},
        **error_responses(
            "/v1/soundwave/render-wavecode",
            "Object not found: personalization/sources/{sourceId}/manifest.json",
            ("SEGMENT_OUT_OF_RANGE", "Segment 7001+3000 ms exceeds measured duration 10000 ms."),
        ),
    },
)
async def soundwave_render(request: Request, body: RenderWavecodeRequest) -> JSONResponse:
    return await run_and_respond(request, body, pipeline.render, "Wavecode rendered")
