"""GET /v1/soundwave/presets, POST /v1/soundwave/process and /render-wavecode."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ...processing import soundwave as pipeline
from ...schemas import (
    Envelope,
    PresetsResponse,
    RenderResponse,
    RenderWavecodeRequest,
    SelectResponse,
    SelectWavecodeStyleRequest,
    SoundwaveRequest,
    SoundwaveResponse,
)
from ..dependencies import require_secret
from ..responses import error_responses, ok_response, run_and_respond

router = APIRouter(tags=["Soundwave"], dependencies=[Depends(require_secret)])


@router.get(
    "/v1/soundwave/presets",
    summary="Danh sách kiểu WAVECODE",
    description="Trả về các preset `style` (CLASSIC, HAIRLINE, OSCILLOSCOPE) cùng SVG mẫu "
    "dựng từ level cố định, dùng cho màn hình chọn kiểu.",
    responses={
        200: {"model": Envelope[PresetsResponse], "description": "Danh sách preset."},
        **{
            k: v
            for k, v in error_responses(
                "/v1/soundwave/presets", "", ("INTERNAL_ERROR", "Internal worker error.")
            ).items()
            if k in (401, 500)
        },
    },
)
async def soundwave_presets(request: Request) -> JSONResponse:
    result = await run_in_threadpool(pipeline.presets)
    return ok_response(request, "Wavecode presets", result.model_dump(mode="json"))


@router.post(
    "/v1/soundwave/process",
    summary="Xử lý audio soundwave",
    description="Giải mã audio, cắt đoạn 3 s tại `segmentStartMs`, tạo WAVECODE "
    "(`wavecode-{style}.svg`, `wavecode-{style}_alpha.png` của kiểu `style`, "
    "`audio_segment.wav`, `manifest.json`).",
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
    description="Render lại WAVECODE từ danh sách 18 level (0..7). Cùng level và đã có file của "
    "kiểu này thì dùng lại (`reused: true`, không ghi file); kiểu mới được thêm cạnh các kiểu cũ; "
    "level mới thì chỉ còn kiểu vừa vẽ.",
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


@router.post(
    "/v1/soundwave/select-wavecode-style",
    summary="Chấp nhận một kiểu WAVECODE",
    description="Giữ lại file của `style`, xóa file của mọi kiểu khác và ghi lại `manifest.json` "
    "(`wavecode.styles` chỉ còn kiểu được chấp nhận, `accepted: true`). Gọi lại là no-op. "
    "Kiểu chưa được tạo thì trả `422 STYLE_NOT_GENERATED`.",
    responses={
        200: {"model": Envelope[SelectResponse], "description": "Đã chấp nhận kiểu (READY)."},
        **error_responses(
            "/v1/soundwave/select-wavecode-style",
            "Object not found: personalization/sources/{sourceId}/manifest.json",
            (
                "STYLE_NOT_GENERATED",
                "Wavecode style HAIRLINE has not been generated for this source.",
            ),
        ),
    },
)
async def soundwave_select_style(
    request: Request, body: SelectWavecodeStyleRequest
) -> JSONResponse:
    return await run_and_respond(request, body, pipeline.select_style, "Wavecode style selected")
