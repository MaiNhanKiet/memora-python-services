"""Soundwave and wavecode request/response models."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from .common import OutputFile, PngOutputFile, PrefixedRequest, check_key

AUDIO_CONTENT_TYPES = frozenset({"audio/wav", "audio/mpeg", "audio/mp4", "audio/aac"})
Level = Annotated[int, Field(ge=0, le=7)]


class SoundwaveRequest(PrefixedRequest):
    rawKey: str
    contentType: str
    declaredDurationMs: int = Field(ge=3000, le=30000)
    segmentStartMs: int = Field(ge=0, le=27000)
    segmentDurationMs: Literal[3000]

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
    levels: list[Level] = Field(min_length=18, max_length=18)


class Wavecode(BaseModel):
    scheme: Literal["MEMORA_WAVECODE_v1"] = "MEMORA_WAVECODE_v1"
    levels: list[int]
    checkBars: list[int]
    bars: list[int]
    nudgeCandidates: list[list[int]]


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
