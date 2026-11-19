from __future__ import annotations

import hmac

from fastapi import Header

from .config import get_settings
from .errors import unauthorized


def require_secret(x_memora_worker_secret: str | None = Header(default=None)) -> None:
    expected = get_settings().worker_shared_secret.encode("utf-8")
    provided = (x_memora_worker_secret or "").encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        raise unauthorized()
