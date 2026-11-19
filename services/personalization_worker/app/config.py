"""Environment configuration with validation. Values are never logged."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

_REQUIRED = (
    "WORKER_SHARED_SECRET",
    "S3_ENDPOINT",
    "S3_REGION",
    "S3_BUCKET",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
)
MIN_SECRET_LENGTH = 16


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    worker_shared_secret: str
    s3_endpoint: str
    s3_region: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    potrace_binary: str = "potrace"
    ffmpeg_binary: str = "ffmpeg"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    values = {name: (source.get(name) or "").strip() for name in _REQUIRED}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError("Missing required environment variables: " + ", ".join(missing))
    if len(values["WORKER_SHARED_SECRET"]) < MIN_SECRET_LENGTH:
        raise ConfigError(f"WORKER_SHARED_SECRET must be at least {MIN_SECRET_LENGTH} characters.")
    if not values["S3_ENDPOINT"].startswith(("http://", "https://")):
        raise ConfigError("S3_ENDPOINT must start with http:// or https://.")
    return Settings(
        worker_shared_secret=values["WORKER_SHARED_SECRET"],
        s3_endpoint=values["S3_ENDPOINT"],
        s3_region=values["S3_REGION"],
        s3_bucket=values["S3_BUCKET"],
        s3_access_key_id=values["S3_ACCESS_KEY_ID"],
        s3_secret_access_key=values["S3_SECRET_ACCESS_KEY"],
        potrace_binary=(source.get("POTRACE_BINARY") or "potrace").strip(),
        ffmpeg_binary=(source.get("FFMPEG_BINARY") or "ffmpeg").strip(),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
