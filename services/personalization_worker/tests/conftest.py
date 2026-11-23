from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.core.errors import object_not_found
from app.main import app
from app.storage import s3 as storage

SECRET = "test-secret-0123456789"
SOURCE_ID = "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c"
PREFIX = f"personalization/sources/{SOURCE_ID}/"
POTRACE_LOCAL = r"D:\1_STUDY\CN8\WDP301\bioring-be\python-services\tools\potrace\potrace.exe"
FFMPEG_LOCAL = r"C:\Users\kietmn\ffmpeg\bin\ffmpeg.exe"


def _binary(env_name: str, default: str, local: str) -> str:
    configured = os.environ.get(env_name)
    if configured:
        return configured
    if shutil.which(default):
        return default
    return local if Path(local).is_file() else default


POTRACE = _binary("POTRACE_BINARY", "potrace", POTRACE_LOCAL)
FFMPEG = _binary("FFMPEG_BINARY", "ffmpeg", FFMPEG_LOCAL)
needs_potrace = pytest.mark.skipif(shutil.which(POTRACE) is None, reason="potrace not available")
needs_ffmpeg = pytest.mark.skipif(shutil.which(FFMPEG) is None, reason="ffmpeg not available")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("WORKER_SHARED_SECRET", SECRET)
    monkeypatch.setenv("S3_ENDPOINT", "http://garage.invalid:3900")
    monkeypatch.setenv("S3_REGION", "garage")
    monkeypatch.setenv("S3_BUCKET", "personalization")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("POTRACE_BINARY", POTRACE)
    monkeypatch.setenv("FFMPEG_BINARY", FFMPEG)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def store(monkeypatch) -> dict[str, tuple[bytes, str]]:
    """In-memory fake of the storage module: key -> (bytes, content type)."""
    objects: dict[str, tuple[bytes, str]] = {}

    def get_object(key: str) -> bytes:
        if key not in objects:
            raise object_not_found(key)
        return objects[key][0]

    def get_object_optional(key: str):
        return objects[key][0] if key in objects else None

    def put_object(key: str, data: bytes, content_type: str):
        objects[key] = (data, content_type)
        return storage.StoredObject(key, hashlib.sha256(data).hexdigest(), len(data), content_type)

    monkeypatch.setattr(storage, "get_object", get_object)
    monkeypatch.setattr(storage, "get_object_optional", get_object_optional)
    monkeypatch.setattr(storage, "put_object", put_object)
    return objects


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-Memora-Worker-Secret": SECRET}
