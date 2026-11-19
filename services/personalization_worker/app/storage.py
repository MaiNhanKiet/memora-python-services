"""S3-compatible object storage (Garage) with path-style addressing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import get_settings
from .errors import WorkerError, object_not_found, storage_unavailable

_NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    sizeBytes: int
    contentType: str


@lru_cache(maxsize=1)
def _client() -> Any:
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        region_name=s.s3_region,
        aws_access_key_id=s.s3_access_key_id,
        aws_secret_access_key=s.s3_secret_access_key,
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def get_object(key: str) -> bytes:
    try:
        response = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)
        return response["Body"].read()
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _NOT_FOUND_CODES:
            raise object_not_found(key) from exc
        raise storage_unavailable() from exc
    except (BotoCoreError, OSError) as exc:
        raise storage_unavailable() from exc


def get_object_optional(key: str) -> bytes | None:
    try:
        return get_object(key)
    except WorkerError as exc:
        if exc.failure_code == "OBJECT_NOT_FOUND":
            return None
        raise


def put_object(key: str, data: bytes, content_type: str) -> StoredObject:
    digest = hashlib.sha256(data).hexdigest()
    try:
        _client().put_object(
            Bucket=get_settings().s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest},
        )
    except (ClientError, BotoCoreError, OSError) as exc:
        raise storage_unavailable() from exc
    return StoredObject(key=key, sha256=digest, sizeBytes=len(data), contentType=content_type)


def check() -> bool:
    try:
        _client().head_bucket(Bucket=get_settings().s3_bucket)
        return True
    except Exception:  # noqa: BLE001 - health probe only
        return False
