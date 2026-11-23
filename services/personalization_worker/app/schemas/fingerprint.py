"""Fingerprint request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from .common import OutputFile, PngOutputFile, PrefixedRequest, check_key


class FingerprintRequest(PrefixedRequest):
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
