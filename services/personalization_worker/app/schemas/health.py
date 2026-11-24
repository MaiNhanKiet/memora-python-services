"""Health response model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthData(BaseModel):
    status: Literal["UP"] = "UP"
    potrace: bool
    ffmpeg: bool
    storage: bool
