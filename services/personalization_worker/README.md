# Personalization processing worker

FastAPI service that turns a raw fingerprint image or audio file stored in Garage (S3 API) into
laser-ready SVG, preview PNG, WAVECODE and a `manifest.json`. Stateless, no database, no queue.

Contract (source of truth, change it there first):
`memora-backend/docs/api/personalization/processing-worker.md`

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness plus `potrace`, `ffmpeg`, `storage` flags. No secret. |
| POST | `/v1/fingerprint/process` | Fingerprint image to `fingerprint.svg` + `fingerprint_alpha.png`. |
| POST | `/v1/soundwave/process` | Audio to 3 s segment, `wavecode.svg`, `wavecode_alpha.png`, `audio_segment.wav`. |
| POST | `/v1/soundwave/render-wavecode` | Re-render a WAVECODE from 18 explicit levels. |

Every `/v1` call needs header `X-Memora-Worker-Secret` (constant-time compare, `401` otherwise).
Responses use the same envelope as the API Gateway:

```json
{ "statusCode": 200, "message": "Fingerprint processed", "data": { "status": "READY", "...": "..." } }
```

`/health` answers `{ "statusCode": 200, "message": "OK", "data": { "status": "UP", "potrace": true, "ffmpeg": true, "storage": true } }`.
Errors (HTTP status unchanged) use
`{ "statusCode": 422, "code": "IMAGE_UNREADABLE", "message": "...", "path": "/v1/...", "timestamp": "2026-01-01T00:00:00.000Z" }`.
Unknown routes and wrong methods use the same shape (`NOT_FOUND`, `METHOD_NOT_ALLOWED`).
An optional `X-Request-Id` request header (max 100 chars, `[A-Za-z0-9_.:-]`) is echoed in the
response header and written to the logs, never to the body.

Request limits: `rawKey` / `outputPrefix` start with `personalization/` (max 1024 chars);
soundwave `contentType` is `audio/wav|mpeg|mp4|aac`, `declaredDurationMs` 3000..30000,
`segmentStartMs` 0..27000.

## Cấu trúc thư mục

```text
app/
  main.py                  create_app(): FastAPI, lifespan, router, handler, bật/tắt /docs (uvicorn app.main:app)
  api/
    routes/                health.py, fingerprint.py, soundwave.py: các endpoint HTTP
    dependencies.py        require_secret + security scheme WorkerSecret (X-Memora-Worker-Secret)
    exception_handlers.py  mọi lỗi (WorkerError, HTTP, validation, bất ngờ) thành error envelope
    middleware.py          X-Request-Id, log context, kiểm secret trước khi báo lỗi body
    responses.py           envelope thành công/lỗi và ví dụ OpenAPI cho từng endpoint
  core/                    config.py (env, Settings), errors.py (mã lỗi), logging.py, version.py
  schemas/                 model Pydantic: common (envelope, key), fingerprint, soundwave, health
  processing/              fingerprint.py, soundwave.py, wavecode.py: pipeline xử lý ảnh/audio
  storage/s3.py            truy cập Garage qua S3 API
tests/
  api/                     test endpoint, envelope, auth, docs
  processing/              test pipeline fingerprint, soundwave, wavecode
```

## Environment

Copy `.env.example` and fill in real values outside git (deploy uses
`deploy/secrets/personalization-worker.env`, gitignored).

| Variable | Meaning |
|---|---|
| `WORKER_SHARED_SECRET` | Shared secret, at least 16 characters |
| `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Garage S3 access (path-style) |
| `POTRACE_BINARY` | Potrace executable, default `potrace` |
| `FFMPEG_BINARY` | FFmpeg executable, default `ffmpeg` |
| `WORKER_DOCS_ENABLED` | `true` serves Swagger UI at `/docs` and the schema at `/openapi.json` (default `false`: both answer 404 in the error envelope). Use Authorize with `X-Memora-Worker-Secret`. Keep it off in production. |

## Run locally

```bash
cd services/personalization_worker
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/pip
# set the variables above (and make potrace + ffmpeg available), then:
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --env-file .env
```

## Tests and lint

```bash
.venv/Scripts/ruff check .
.venv/Scripts/python -m pytest -q
```

Storage is faked in tests. Tests that need the `potrace` / `ffmpeg` binaries are skipped when
they are not found (set `POTRACE_BINARY` / `FFMPEG_BINARY` to point at them).

## Docker

```bash
docker build -t memora-personalization-worker services/personalization_worker
```

The image installs `potrace` and `ffmpeg`, runs as a non-root user and listens on port 8010.
No secrets are baked in; pass them with `--env-file`. Compose definition: `deploy/compose.yaml`.
