"""Local developer test UI server (NOT for deployment).

Run with the worker venv:
    services/personalization_worker/.venv/Scripts/python tools/test-ui/server.py

Routes:
    /                 static UI (tools/test-ui/static)
    /api/*            proxy to the API Gateway (cookies kept server-side per account slot)
    /worker/*         proxy to the Python worker, adds X-Memora-Worker-Secret
    /dev/presign      short-lived presigned GET for personalization/* objects
    /dev/upload-raw   put a local file under personalization/test-ui/ in the bucket
                      (optional explicit ?key= must also live under that prefix)
    /dev/delete-prefix POST ?prefix=personalization/test-ui/... deletes every object under it
    /dev/list         GET ?prefix=personalization/test-ui/... lists up to 1000 keys (key, size, lastModified)
    /dev/object       GET ?key=personalization/... streams an object from the bucket (same origin, no CORS)
    /models/<NAME>.glb and /models/<NAME>/template.json
                      read-only 3D model files from MODELS_DIR (NAME matches ^[A-Z0-9_]+$)
    /dev/config       non-secret configuration summary

Binds 127.0.0.1 only. Secrets are read from the worker .env and never sent to the browser.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import re
import threading
import uuid
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
HOST = "127.0.0.1"

# ---------------------------------------------------------------- configuration
_local_env = {k: v for k, v in dotenv_values(HERE / ".env").items() if v is not None}


def _cfg(name: str, default: str) -> str:
    return os.environ.get(name) or _local_env.get(name) or default


GATEWAY_URL = _cfg("GATEWAY_URL", "http://localhost:3000").rstrip("/")
WORKER_URL = _cfg("WORKER_URL", "http://127.0.0.1:8010").rstrip("/")
PORT = int(_cfg("TEST_UI_PORT", "8090"))
_worker_env_path = Path(
    _cfg("WORKER_ENV_PATH", "../../services/personalization_worker/.env")
)
if not _worker_env_path.is_absolute():
    _worker_env_path = (HERE / _worker_env_path).resolve()
# Values stay inside this process; they are never printed or returned to the browser.
WORKER_ENV = {k: v for k, v in dotenv_values(_worker_env_path).items() if v is not None}

MAX_UPLOAD_BYTES = 60 * 1024 * 1024
# Read-only 3D models: <MODELS_DIR>/<NAME>/<NAME>.glb and <NAME>_template.json.
MODELS_DIR = Path(
    _cfg("MODELS_DIR", str(HERE.parent.parent.parent / "memora-3d"))
).resolve()
MODEL_NAME = re.compile(r"^[A-Z0-9_]+$")
KEY_PREFIX = "personalization/"
RAW_PREFIX = "personalization/test-ui/"

# Headers copied browser -> upstream (everything else is dropped on purpose).
FORWARD_REQUEST_HEADERS = (
    "authorization",
    "content-type",
    "accept",
    "x-csrf-token",
    "idempotency-key",
    "x-request-id",
)
# Headers copied upstream -> browser.
FORWARD_RESPONSE_HEADERS = ("content-type", "cache-control", "x-request-id")

# Per-slot cookie jars (customer / staff) so two accounts do not overwrite each other.
_jars: dict[str, dict[str, str]] = {}
_jar_lock = threading.Lock()

_s3_client = None


def _s3():
    """Lazily build a path-style, sigv4 boto3 client from the worker S3_* settings."""
    global _s3_client
    if _s3_client is None:
        import boto3
        from botocore.config import Config

        _s3_client = boto3.client(
            "s3",
            endpoint_url=WORKER_ENV.get("S3_ENDPOINT"),
            region_name=WORKER_ENV.get("S3_REGION") or "garage",
            aws_access_key_id=WORKER_ENV.get("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=WORKER_ENV.get("S3_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    return _s3_client


def _valid_key(key: str, prefix: str) -> bool:
    return (
        key.startswith(prefix)
        and ".." not in key
        and "\\" not in key
        and "//" not in key
        and len(key) < 400
    )


# ---------------------------------------------------------------- cookie jar helpers
def _jar_cookie_header(slot: str) -> str | None:
    with _jar_lock:
        jar = _jars.get(slot)
        if not jar:
            return None
        return "; ".join(f"{k}={v}" for k, v in jar.items())


def _jar_store(slot: str, set_cookie_values: list[str]) -> None:
    """Keep name=value of every Set-Cookie; drop attributes (Domain/Secure/SameSite)."""
    with _jar_lock:
        jar = _jars.setdefault(slot, {})
        for raw in set_cookie_values:
            parts = [p.strip() for p in raw.split(";")]
            name, _, value = parts[0].partition("=")
            attrs = {p.split("=")[0].lower(): p.partition("=")[2] for p in parts[1:]}
            expired = attrs.get("max-age", "1").strip() == "0" or value == ""
            if expired:
                jar.pop(name, None)
            else:
                jar[name] = value


class Handler(BaseHTTPRequestHandler):
    server_version = "memora-test-ui"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401 - quiet, path only
        path = urlsplit(self.path).path
        print(f"{self.command} {path} -> {args[1] if len(args) > 1 else ''}")

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("body too large")
        return self.rfile.read(length) if length else b""

    # ------------------------------------------------------------ dispatch
    def do_GET(self) -> None:
        self._route()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_GET

    def _route(self) -> None:
        try:
            parts = urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/") or path == "/api":
                self._proxy(GATEWAY_URL, path, parts.query, slot=True, secret=False)
            elif path.startswith("/worker/"):
                self._proxy(
                    WORKER_URL,
                    path[len("/worker") :],
                    parts.query,
                    slot=False,
                    secret=True,
                )
            elif path == "/dev/presign" and self.command == "GET":
                self._presign(parse_qs(parts.query))
            elif path == "/dev/object" and self.command == "GET":
                self._object(parse_qs(parts.query))
            elif path == "/dev/list" and self.command == "GET":
                self._list(parse_qs(parts.query))
            elif path.startswith("/models/"):
                self._model(path)
            elif path == "/dev/upload-raw" and self.command == "POST":
                self._upload_raw(parse_qs(parts.query))
            elif path == "/dev/delete-prefix" and self.command == "POST":
                self._delete_prefix(parse_qs(parts.query))
            elif path == "/dev/config" and self.command == "GET":
                self._send_json(
                    200,
                    {
                        "gatewayUrl": GATEWAY_URL,
                        "workerUrl": WORKER_URL,
                        "bucket": WORKER_ENV.get("S3_BUCKET"),
                        "workerSecretLoaded": bool(
                            WORKER_ENV.get("WORKER_SHARED_SECRET")
                        ),
                        "s3Configured": bool(WORKER_ENV.get("S3_ENDPOINT")),
                    },
                )
            elif self.command == "GET":
                self._static(path)
            else:
                self._send_json(404, {"error": "not_found"})
        except ValueError as exc:
            self._send_json(413, {"error": "bad_request", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - dev tool: surface a clear error, never crash
            self._send_json(500, {"error": "internal", "message": type(exc).__name__})

    # ------------------------------------------------------------ static files
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not target.is_relative_to(STATIC_DIR) or not target.is_file():
            self._send_json(404, {"error": "not_found"})
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"{ctype}; charset=utf-8"
            if ctype.startswith("text/") or ctype.endswith("javascript")
            else ctype,
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ proxy
    def _proxy(
        self, base: str, path: str, query: str, *, slot: bool, secret: bool
    ) -> None:
        try:
            body = self._read_body()
        except ValueError as exc:
            self._send_json(413, {"error": "bad_request", "message": str(exc)})
            return
        target = urlsplit(base)
        headers = {
            k: v
            for k, v in ((h, self.headers.get(h)) for h in FORWARD_REQUEST_HEADERS)
            if v
        }
        slot_name = (self.headers.get("X-Test-Slot") or "").strip()
        if slot and slot_name:
            cookie = _jar_cookie_header(slot_name)
            if cookie:
                headers["Cookie"] = cookie
        if secret:
            headers["X-Memora-Worker-Secret"] = WORKER_ENV.get(
                "WORKER_SHARED_SECRET", ""
            )
        url = path + (f"?{query}" if query else "")
        conn_cls = (
            http.client.HTTPSConnection
            if target.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = conn_cls(target.hostname, target.port, timeout=120)
        try:
            conn.request(self.command, url, body=body or None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            set_cookies = resp.msg.get_all("Set-Cookie") or []
        except (OSError, http.client.HTTPException) as exc:
            self._send_json(
                502,
                {
                    "error": "upstream_unreachable",
                    "message": f"Khong ket noi duoc toi {base} ({type(exc).__name__})",
                },
            )
            return
        finally:
            conn.close()
        if slot and slot_name and set_cookies:
            _jar_store(slot_name, set_cookies)  # cookies stay server-side
        self.send_response(resp.status)
        for name in FORWARD_RESPONSE_HEADERS:
            value = resp.getheader(name)
            if value:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ presign
    def _presign(self, query: dict[str, list[str]]) -> None:
        key = (query.get("key") or [""])[0]
        if not _valid_key(key, KEY_PREFIX):
            self._send_json(
                400,
                {
                    "error": "invalid_key",
                    "message": "key must start with personalization/ and not contain ..",
                },
            )
            return
        if not WORKER_ENV.get("S3_BUCKET"):
            self._send_json(503, {"error": "s3_not_configured"})
            return
        url = _s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": WORKER_ENV["S3_BUCKET"], "Key": key},
            ExpiresIn=300,
        )
        self._send_json(200, {"url": url})

    # ------------------------------------------------------------ list keys under a test prefix
    def _list(self, query: dict[str, list[str]]) -> None:
        prefix = (query.get("prefix") or [""])[0]
        if not (
            prefix.startswith(RAW_PREFIX)
            and len(prefix) <= 1024
            and ".." not in prefix.split("/")
            and "\\" not in prefix
        ):
            self._send_json(
                400,
                {
                    "error": "invalid_prefix",
                    "message": f"prefix must start with {RAW_PREFIX} (no .., max 1024 chars)",
                },
            )
            return
        if not WORKER_ENV.get("S3_BUCKET"):
            self._send_json(503, {"error": "s3_not_configured"})
            return
        try:
            page = _s3().list_objects_v2(
                Bucket=WORKER_ENV["S3_BUCKET"], Prefix=prefix, MaxKeys=1000
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                502, {"error": "list_failed", "message": type(exc).__name__}
            )
            return
        keys = [
            {
                "key": o["Key"],
                "size": o["Size"],
                "lastModified": o["LastModified"].isoformat(),
            }
            for o in page.get("Contents", [])
        ]
        self._send_json(
            200,
            {
                "prefix": prefix,
                "keys": keys,
                "truncated": bool(page.get("IsTruncated")),
            },
        )

    # ------------------------------------------------------------ stream a bucket object
    def _object(self, query: dict[str, list[str]]) -> None:
        key = (query.get("key") or [""])[0]
        if not (
            key.startswith(KEY_PREFIX)
            and len(key) <= 1024
            and ".." not in key.split("/")
            and "\\" not in key
            and "//" not in key
        ):
            self._send_json(
                400,
                {
                    "error": "invalid_key",
                    "message": "key must start with personalization/ and not contain ..",
                },
            )
            return
        if not WORKER_ENV.get("S3_BUCKET"):
            self._send_json(503, {"error": "s3_not_configured"})
            return
        try:
            obj = _s3().get_object(Bucket=WORKER_ENV["S3_BUCKET"], Key=key)
        except Exception as exc:  # noqa: BLE001
            missing = "NoSuchKey" in type(exc).__name__ or "404" in str(exc)
            self._send_json(404 if missing else 502, {"error": "object_unavailable"})
            return
        self.send_response(200)
        self.send_header(
            "Content-Type", obj.get("ContentType") or "application/octet-stream"
        )
        self.send_header("Content-Length", str(obj["ContentLength"]))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        for chunk in obj["Body"].iter_chunks(64 * 1024):
            self.wfile.write(chunk)

    # ------------------------------------------------------------ 3D models (read-only)
    def _model(self, path: str) -> None:
        glb = re.fullmatch(r"/models/([A-Z0-9_]+)\.glb", path)
        tpl = re.fullmatch(r"/models/([A-Z0-9_]+)/template\.json", path)
        if glb:
            name, filename, ctype = (
                glb.group(1),
                f"{glb.group(1)}.glb",
                "model/gltf-binary",
            )
        elif tpl:
            name, filename = tpl.group(1), f"{tpl.group(1)}_template.json"
            ctype = "application/json; charset=utf-8"
        else:
            self._send_json(404, {"error": "not_found"})
            return
        target = (MODELS_DIR / name / filename).resolve()
        if not target.is_relative_to(MODELS_DIR) or not target.is_file():
            self._send_json(404, {"error": "not_found"})
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ delete by prefix
    def _delete_prefix(self, query: dict[str, list[str]]) -> None:
        prefix = (query.get("prefix") or [""])[0]
        if not (
            prefix.startswith(RAW_PREFIX)
            and len(prefix) <= 1024
            and ".." not in prefix.split("/")
            and "\\" not in prefix
        ):
            self._send_json(
                400,
                {
                    "error": "invalid_prefix",
                    "message": f"prefix must start with {RAW_PREFIX} (no .., max 1024 chars)",
                },
            )
            return
        if not WORKER_ENV.get("S3_BUCKET"):
            self._send_json(503, {"error": "s3_not_configured"})
            return
        bucket = WORKER_ENV["S3_BUCKET"]
        deleted = 0
        paginator = _s3().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                _s3().delete_objects(
                    Bucket=bucket, Delete={"Objects": keys, "Quiet": True}
                )
                deleted += len(keys)
        self._send_json(200, {"deleted": deleted})

    # ------------------------------------------------------------ raw upload
    def _upload_raw(self, query: dict[str, list[str]]) -> None:
        if not WORKER_ENV.get("S3_BUCKET"):
            self._send_json(503, {"error": "s3_not_configured"})
            return
        try:
            body = self._read_body()
        except ValueError as exc:
            self._send_json(413, {"error": "bad_request", "message": str(exc)})
            return
        ctype_header = self.headers.get("Content-Type") or ""
        content_type = (query.get("contentType") or [""])[0]
        if ctype_header.lower().startswith("multipart/form-data"):
            msg = BytesParser(policy=HTTP).parsebytes(
                b"Content-Type: " + ctype_header.encode() + b"\r\n\r\n" + body
            )
            file_part = next((p for p in msg.iter_parts() if p.get_filename()), None)
            if file_part is None:
                self._send_json(
                    400,
                    {"error": "no_file", "message": "multipart without a file part"},
                )
                return
            body = file_part.get_payload(decode=True) or b""
            content_type = content_type or file_part.get_content_type()
        elif not content_type:
            content_type = ctype_header or "application/octet-stream"
        if not body:
            self._send_json(400, {"error": "empty_body"})
            return
        key = (query.get("key") or [""])[0] or f"{RAW_PREFIX}{uuid.uuid4()}/raw"
        if not _valid_key(key, RAW_PREFIX):
            self._send_json(
                400,
                {
                    "error": "invalid_key",
                    "message": f"key must start with {RAW_PREFIX}",
                },
            )
            return
        _s3().put_object(
            Bucket=WORKER_ENV["S3_BUCKET"], Key=key, Body=body, ContentType=content_type
        )
        self._send_json(
            200,
            {
                "key": key,
                "sha256": hashlib.sha256(body).hexdigest(),
                "sizeBytes": len(body),
            },
        )


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(
        f"MEMORA test UI: http://{HOST}:{PORT}  gateway={GATEWAY_URL}  worker={WORKER_URL}"
    )
    print(f"worker env loaded: {bool(WORKER_ENV)} (values never printed)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
