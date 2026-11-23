from __future__ import annotations

import hmac

from fastapi import Depends
from fastapi.security import APIKeyHeader

from ..core.config import get_settings
from ..core.errors import unauthorized

secret_header = APIKeyHeader(
    name="X-Memora-Worker-Secret",
    scheme_name="WorkerSecret",
    auto_error=False,
    description="Shared secret between Operations and the worker (WORKER_SHARED_SECRET).",
)


def require_secret(provided_secret: str | None = Depends(secret_header)) -> None:
    expected = get_settings().worker_shared_secret.encode("utf-8")
    provided = (provided_secret or "").encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise unauthorized()
