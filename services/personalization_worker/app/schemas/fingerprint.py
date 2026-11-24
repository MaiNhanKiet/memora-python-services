"""Fingerprint request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import OutputFile, PngOutputFile, PrefixedRequest, check_key


class FingerprintRequest(PrefixedRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sourceId": "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c",
                    "rawKey": "personalization/fingerprint/{orderId}/sessions/{sessionId}/a-1.bmp",
                    "outputPrefix": "personalization/fingerprint/{orderId}/selected/{sourceId}/",
                    "context": {
                        "orderId": "7d0a9c52-1b3e-4f6a-8c21-5e9d4b7a1f30",
                        "sessionId": "b2c4e6a8-0f1d-4a3b-9c5e-7d8f1a2b3c4d",
                        "attemptNo": 1,
                        "ringItemIds": ["c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"],
                    },
                }
            ]
        }
    )

    rawKey: str

    @field_validator("rawKey")
    @classmethod
    def _raw(cls, value: str) -> str:
        return check_key(value)


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


FrameShape = Literal["NATURAL", "CIRCLE", "HEART", "SQUARE"]
VERSION_ID_PATTERN = r"^[a-z0-9-]{1,40}$"
MAX_STROKES = 50
MAX_STROKE_POINTS = 200


class EraseStroke(BaseModel):
    """One eraser stroke in normalized base-image coordinates (0..1, origin top-left)."""

    model_config = ConfigDict(extra="forbid")

    radius: float = Field(ge=0.005, le=0.1)
    points: list[list[float]] = Field(min_length=1, max_length=MAX_STROKE_POINTS)

    @field_validator("points")
    @classmethod
    def _points(cls, value: list[list[float]]) -> list[list[float]]:
        for point in value:
            if len(point) != 2 or not all(0.0 <= v <= 1.0 for v in point):
                raise ValueError("each point must be [x, y] within 0..1")
        return value


class BaseImage(BaseModel):
    key: str
    sha256: str
    sizeBytes: int
    widthPx: int
    heightPx: int


class Version(BaseModel):
    versionId: str
    shape: FrameShape
    offsetX: float
    offsetY: float
    zoom: float
    erasedRatio: float
    ridgeWidthRatio: float
    minEngraveSizeMm: float
    coverage: float


class FingerprintResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    outputs: FingerprintOutputs
    artwork: Artwork
    quality: Quality
    version: Version
    base: BaseImage


class RenderCropRequest(PrefixedRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sourceId": "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c",
                    "outputPrefix": "personalization/fingerprint/{orderId}/selected/{sourceId}/",
                    "versionId": "v1",
                    "shape": "CIRCLE",
                    "offsetX": 0.05,
                    "offsetY": -0.1,
                    "zoom": 1.4,
                    "eraseStrokes": [{"radius": 0.02, "points": [[0.12, 0.2], [0.14, 0.22]]}],
                }
            ]
        }
    )

    versionId: str = Field(pattern=VERSION_ID_PATTERN)
    shape: FrameShape
    offsetX: float = Field(ge=-1, le=1)
    offsetY: float = Field(ge=-1, le=1)
    zoom: float = Field(ge=1, le=3)
    eraseStrokes: list[EraseStroke] = Field(default_factory=list, max_length=MAX_STROKES)


class SelectVersionRequest(PrefixedRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sourceId": "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c",
                    "outputPrefix": "personalization/fingerprint/{orderId}/selected/{sourceId}/",
                    "versionId": "v1",
                }
            ]
        }
    )

    versionId: str = Field(pattern=VERSION_ID_PATTERN)


class RenderCropResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    outputs: FingerprintOutputs
    artwork: Artwork
    version: Version
    base: BaseImage


class SelectVersionResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    outputs: FingerprintOutputs
    artwork: Artwork
    version: Version
    deleted: list[str]


class FrameShapePreset(BaseModel):
    code: FrameShape
    name: str
    description: str
    sampleSvg: str


class FingerprintPresetsResponse(BaseModel):
    defaultShape: FrameShape
    shapes: list[FrameShapePreset]
