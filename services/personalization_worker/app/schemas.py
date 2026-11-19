"""Pydantic models matching the contract JSON exactly (camelCase)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

Level = Annotated[int, Field(ge=0, le=7)]


def _check_key(value: str) -> str:
    if not value or value.startswith("/") or ".." in value.split("/") or "\\" in value:
        raise ValueError("invalid object key")
    return value


class _Base(BaseModel):
    sourceId: str

    @field_validator("sourceId")
    @classmethod
    def _uuid(cls, value: str) -> str:
        uuid.UUID(value)
        return value


class _WithPrefix(_Base):
    outputPrefix: str

    @field_validator("outputPrefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        _check_key(value)
        if not value.endswith("/"):
            raise ValueError("outputPrefix must end with '/'")
        return value


class FingerprintRequest(_WithPrefix):
    rawKey: str

    @field_validator("rawKey")
    @classmethod
    def _raw(cls, value: str) -> str:
        return _check_key(value)


class SoundwaveRequest(_WithPrefix):
    rawKey: str
    contentType: str = Field(min_length=1)
    declaredDurationMs: int = Field(gt=0)
    segmentStartMs: int = Field(ge=0)
    segmentDurationMs: Literal[3000]

    @field_validator("rawKey")
    @classmethod
    def _raw(cls, value: str) -> str:
        return _check_key(value)


class RenderWavecodeRequest(_WithPrefix):
    levels: list[Level] = Field(min_length=18, max_length=18)


class OutputFile(BaseModel):
    key: str
    sha256: str
    sizeBytes: int
    contentType: str


class PngOutputFile(OutputFile):
    widthPx: int
    heightPx: int


class FingerprintOutputs(BaseModel):
    vectorSvg: OutputFile
    alphaPng: PngOutputFile
    manifest: OutputFile


class Artwork(BaseModel):
    aspectRatio: float
    hull: list[list[float]]


class Quality(BaseModel):
    score: float
    message: str


class FingerprintResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    outputs: FingerprintOutputs
    artwork: Artwork
    quality: Quality


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
