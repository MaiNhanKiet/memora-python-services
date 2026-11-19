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
Errors use `{ "status": "FAILED", "failureCode": "...", "message": "..." }`.

## Environment

Copy `.env.example` and fill in real values outside git (deploy uses
`deploy/secrets/personalization-worker.env`, gitignored).

| Variable | Meaning |
|---|---|
| `WORKER_SHARED_SECRET` | Shared secret, at least 16 characters |
| `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Garage S3 access (path-style) |
| `POTRACE_BINARY` | Potrace executable, default `potrace` |
| `FFMPEG_BINARY` | FFmpeg executable, default `ffmpeg` |

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
