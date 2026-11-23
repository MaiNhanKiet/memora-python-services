"""Fingerprint request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

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


class FingerprintResponse(BaseModel):
    status: Literal["READY"] = "READY"
    processorVersion: str
    outputs: FingerprintOutputs
    artwork: Artwork
    quality: Quality
