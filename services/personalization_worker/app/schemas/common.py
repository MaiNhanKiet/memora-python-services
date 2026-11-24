"""Shared schema pieces: envelopes, output file descriptors and object key validation."""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")

KEY_ROOT = "personalization/"
KEY_MAX_LENGTH = 1024


def check_key(value: str) -> str:
    if not value or value.startswith("/") or ".." in value.split("/") or "\\" in value:
        raise ValueError("invalid object key")
    if len(value) > KEY_MAX_LENGTH:
        raise ValueError("object key too long")
    if not value.startswith(KEY_ROOT):
        raise ValueError(f"object key must start with '{KEY_ROOT}'")
    return value


class RequestContext(BaseModel):
    """Opaque business context echoed verbatim into manifest.json (never used for processing)."""

    model_config = ConfigDict(extra="forbid")

    orderId: str
    sessionId: str | None = None
    attemptNo: int | None = Field(default=None, ge=1, le=3)
    ringItemIds: list[str] | None = Field(default=None, max_length=2)

    @field_validator("orderId", "sessionId")
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        if value is not None:
            uuid.UUID(value)
        return value

    @field_validator("ringItemIds")
    @classmethod
    def _ring_items(cls, value: list[str] | None) -> list[str] | None:
        for item in value or []:
            uuid.UUID(item)
        return value


class SourceRequest(BaseModel):
    sourceId: str
    context: RequestContext | None = None

    @field_validator("sourceId")
    @classmethod
    def _uuid(cls, value: str) -> str:
        uuid.UUID(value)
        return value


class PrefixedRequest(SourceRequest):
    outputPrefix: str

    @field_validator("outputPrefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        check_key(value)
        if not value.endswith("/"):
            raise ValueError("outputPrefix must end with '/'")
        return value


class OutputFile(BaseModel):
    key: str
    sha256: str
    sizeBytes: int
    contentType: str


class PngOutputFile(OutputFile):
    widthPx: int
    heightPx: int


class Envelope(BaseModel, Generic[T]):
    """Success envelope shared with the API Gateway."""

    statusCode: int = 200
    message: str
    data: T


class ErrorEnvelope(BaseModel):
    """Error envelope shared with the API Gateway."""

    statusCode: int
    code: str
    message: str
    path: str
    timestamp: str
