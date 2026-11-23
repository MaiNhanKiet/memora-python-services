"""Soundwave and wavecode request/response models."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..processing.wavecode_styles import DEFAULT_STYLE, StyleCode
from .common import OutputFile, PngOutputFile, PrefixedRequest, check_key

AUDIO_CONTENT_TYPES = frozenset({"audio/wav", "audio/mpeg", "audio/mp4", "audio/aac"})
Level = Annotated[int, Field(ge=0, le=7)]


class SoundwaveRequest(PrefixedRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sourceId": "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c",
                    "rawKey": "personalization/soundwave/{orderId}/uploads/{sourceId}/raw.wav",
                    "outputPrefix": "personalization/soundwave/{orderId}/selected/{sourceId}/",
                    "contentType": "audio/wav",
                    "declaredDurationMs": 10000,
                    "segmentStartMs": 2000,
                    "segmentDurationMs": 3000,
                    "style": "CLASSIC",
                    "context": {
                        "orderId": "7d0a9c52-1b3e-4f6a-8c21-5e9d4b7a1f30",
                        "ringItemIds": ["c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"],
                    },
                }
            ]
        }
    )

    rawKey: str
    contentType: str
    declaredDurationMs: int = Field(ge=3000, le=30000)
    segmentStartMs: int = Field(ge=0, le=27000)
    segmentDurationMs: Literal[3000]
    style: StyleCode = DEFAULT_STYLE

    @field_validator("contentType")
    @classmethod
    def _content_type(cls, value: str) -> str:
        if value not in AUDIO_CONTENT_TYPES:
            raise ValueError("unsupported contentType")
        return value

    @field_validator("rawKey")
    @classmethod
    def _raw(cls, value: str) -> str:
        return check_key(value)


class RenderWavecodeRequest(PrefixedRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sourceId": "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c",
                    "outputPrefix": "personalization/soundwave/{orderId}/selected/{sourceId}/",
                    "levels": [3, 5, 6, 4, 2, 1, 4, 6, 7, 5, 3, 2, 4, 5, 6, 3, 2, 1],
                    "style": "HAIRLINE",
                    "context": {
                        "orderId": "7d0a9c52-1b3e-4f6a-8c21-5e9d4b7a1f30",
                        "ringItemIds": ["c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"],
                    },
                }
            ]
        }
    )

    levels: list[Level] = Field(min_length=18, max_length=18)
    # Omitted: keep the previous manifest's style, else CLASSIC.
    style: StyleCode | None = Field(
        default=None,
        description=(
            "Kiểu hiển thị: CLASSIC (thanh 0.16 mm + đường tâm), HAIRLINE (thanh 0.10 mm), "
            "OSCILLOSCOPE (nét sóng liền). Bỏ trống: giữ kiểu hiện tại trong manifest, "
            "nếu chưa có thì CLASSIC. Kiểu đã tạo với cùng levels thì dùng lại (reused: true)."
        ),
    )


class StyleFiles(BaseModel):
    vectorSvg: OutputFile
    alphaPng: PngOutputFile


class Wavecode(BaseModel):
    scheme: Literal["MEMORA_WAVECODE_v1"] = "MEMORA_WAVECODE_v1"
    style: StyleCode = DEFAULT_STYLE
    levels: list[int]
    checkBars: list[int]
    bars: list[int]
    nudgeCandidates: list[list[int]]
    styles: dict[str, StyleFiles] = Field(default_factory=dict)


class SoundwaveOutputs(BaseModel):
    vectorSvg: OutputFile
    alphaPng: PngOutputFile
    audioSegment: OutputFile
    manifest: OutputFile


class SoundwaveResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    measuredDurationMs: int
    outputs: SoundwaveOutputs
    wavecode: Wavecode


class RenderOutputs(BaseModel):
    vectorSvg: OutputFile
    alphaPng: PngOutputFile
    manifest: OutputFile


class RenderResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    outputs: RenderOutputs
    wavecode: Wavecode
    reused: bool = False


class SelectWavecodeStyleRequest(PrefixedRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sourceId": "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c",
                    "outputPrefix": "personalization/soundwave/{orderId}/selected/{sourceId}/",
                    "style": "HAIRLINE",
                }
            ]
        }
    )

    style: StyleCode


class SelectResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    outputs: RenderOutputs
    wavecode: Wavecode
    deleted: list[str]


class PresetStyle(BaseModel):
    code: StyleCode
    name: str
    description: str
    decoder: Literal["BAR_HEIGHT", "PEAK_HEIGHT"]
    sampleSvg: str


class PresetsResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "defaultStyle": "CLASSIC",
                    "styles": [
                        {
                            "code": "CLASSIC",
                            "name": "Cổ điển",
                            "description": "Các thanh bo tròn rộng 0,16 mm nối với nhau bằng "
                            "một đường tâm mảnh.",
                            "decoder": "BAR_HEIGHT",
                            "sampleSvg": '<?xml version="1.0" encoding="UTF-8"?>\n<svg ...>',
                        }
                    ],
                }
            ]
        }
    )

    defaultStyle: StyleCode
    styles: list[PresetStyle]
