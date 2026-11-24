# Personalization processing worker

FastAPI service that turns a raw fingerprint image or audio file stored in Garage (S3 API) into
laser-ready SVG, preview PNG, WAVECODE and a `manifest.json`. Stateless, no database, no queue.

Contract (source of truth, change it there first):
`memora-backend/docs/api/personalization/processing-worker.md`

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness plus `potrace`, `ffmpeg`, `storage` flags. No secret. |
| POST | `/v1/fingerprint/process` | Fingerprint image to `base.png` (~2048 px cleaned) and default version `v0` (`versions/v0/fingerprint.svg` + `fingerprint_alpha.png`). |
| GET | `/v1/fingerprint/presets` | Frame presets (NATURAL, CIRCLE, HEART, SQUARE) with a sample SVG each. |
| POST | `/v1/fingerprint/render-crop` | Frame + offset + zoom + eraser strokes on `base.png` into a new version. |
| POST | `/v1/fingerprint/select-version` | Accept one version: delete the other versions and `base.png`. |
| GET | `/v1/soundwave/presets` | Wavecode style presets with a sample SVG each (secret required). |
| POST | `/v1/soundwave/process` | Audio to 3 s segment, `wavecode-{style}.svg`, `wavecode-{style}_alpha.png` (requested style only), `audio_segment.wav`. |
| POST | `/v1/soundwave/render-wavecode` | Render a WAVECODE style from 18 explicit levels; reuses existing files, adds styles. |
| POST | `/v1/soundwave/select-wavecode-style` | Accept one style: keep its files, delete the other styles' files. |

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

Every `/v1` request body may carry an optional `context` object, copied verbatim into
`manifest.json` as `"context"` (omitted when absent; render-wavecode keeps the previous manifest's
context when the request has none). Unknown keys inside `context` are rejected with `400`:

```json
{
  "sourceId": "3f2b6c1e-8a4d-4c55-9a77-0d1e2f3a4b5c",
  "rawKey": "personalization/fingerprint/{orderId}/sessions/{sessionId}/attempt-1.bmp",
  "outputPrefix": "personalization/fingerprint/{orderId}/selected/{sourceId}/",
  "context": {
    "orderId": "uuid",
    "sessionId": "uuid (optional)",
    "attemptNo": 1,
    "ringItemIds": ["uuid", "uuid (max 2)"]
  }
}
```

Request limits: `rawKey` / `outputPrefix` start with `personalization/` (max 1024 chars);
soundwave `contentType` is `audio/wav|mpeg|mp4|aac`, `declaredDurationMs` 3000..30000,
`segmentStartMs` 0..27000.

## Wavecode style presets

`SoundwaveRequest` and `RenderWavecodeRequest` accept an optional `style`. The encoding
`MEMORA_WAVECODE_v1` (23 bars, levels, check and reference bars, pitch 0.3696 mm, centred on the
8.5 x 2.0 mm canvas, level-to-height table) is identical for every style; only the drawing differs.
The SVG root carries `data-style="<CODE>"`; the PNG is always 2125x500 transparent.

| `style` | Name | Drawing | Decoder |
|---|---|---|---|
| `CLASSIC` (default) | Cổ điển | 23 rects 0.16 mm wide (rx 0.08) plus a 0.08 mm centre line | `BAR_HEIGHT` |
| `HAIRLINE` | Nét mảnh | 23 rects 0.10 mm wide (rx 0.05), no centre line | `BAR_HEIGHT` |
| `OSCILLOSCOPE` | Sóng liền | one `<polyline>` (25 points: start at bar 0 centre - 0.15 mm, bar peaks alternating up/down starting upwards, end at bar 22 centre + 0.15 mm, y = 1.0), `fill="none"`, stroke 0.09 mm, round joins and caps | `PEAK_HEIGHT` |

OSCILLOSCOPE is a stroked line, not a filled shape: laser software engraves it as a 0.09 mm line.
The response `wavecode.style` and `manifest.json` (`wavecode.style`) record the style used.
`render-wavecode` without `style` keeps the previous manifest's style (like `context`), else
`CLASSIC`. An unknown value returns `400 INVALID_REQUEST`. `GET /v1/soundwave/presets` returns
`{ "defaultStyle": "CLASSIC", "styles": [{ "code", "name", "description", "decoder", "sampleSvg" }] }`
where `sampleSvg` renders the fixed demo levels `[3,5,6,4,2,1,4,6,7,5,3,2,4,5,6,3,2,1]`.
Definitions live in `app/processing/wavecode_styles.py` (single source of truth).

### Per-style files, reuse and acceptance

Files under `outputPrefix` (`.../selected/{sourceId}/`), for each generated style `S` (lower-case code):
`wavecode-{s}.svg`, `wavecode-{s}_alpha.png`, plus `audio_segment.wav` and `manifest.json`.
The old names `wavecode.svg` / `wavecode_alpha.png` are no longer written.

`manifest.json` `wavecode`: `{ scheme, levels, checkBars, bars, style, styles }` where `style` is the
current style and `styles` maps each generated code to
`{ vectorSvg: {key, sha256, sizeBytes, contentType}, alphaPng: {key, sha256, sizeBytes, contentType, widthPx, heightPx} }`.
The manifest top-level `outputs.vectorSvg` / `outputs.alphaPng` point at the current style.

- `process` writes only the requested style (default `CLASSIC`); response `wavecode.styles` has one entry.
- `render-wavecode` resolves the style (request, previous manifest style, `CLASSIC`). If the manifest already
  has `styles[style]`, its `wavecode.levels` equal the request levels and both objects still exist, the files
  are **reused**: nothing is rendered or written for them, only `wavecode.style` is updated, response
  `reused: true`. Otherwise the style is rendered and added (other styles' files are never touched); the same
  style with different levels overwrites its own files and resets `styles` to that style alone.
  The response is the same shape as `process` plus `reused`, `wavecode.styles` listing the whole manifest.
- `select-wavecode-style` `{ sourceId, outputPrefix, style, context? }`: requires `styles[style]` and both
  files, else `422 STYLE_NOT_GENERATED`. Deletes the files of every other style (those in the manifest and
  the canonical names, missing keys ignored), rewrites the manifest (`wavecode.styles` only the accepted
  style, `wavecode.style` = accepted, `"accepted": true`). Idempotent. Response data:
  `{ status, processorVersion, outputs: {vectorSvg, alphaPng, manifest}, wavecode, deleted: [keys] }`.
  A missing manifest is `404 OBJECT_NOT_FOUND`.

## Fingerprint artwork design

Layout under `outputPrefix`: `base.png`, `versions/{versionId}/fingerprint.svg` and
`fingerprint_alpha.png`, `manifest.json`. There is no top-level `fingerprint.svg` any more; the
response `outputs.vectorSvg` / `alphaPng` point at the current version (`v0` after `process`).

- **Cleanup** (in `process`): after small-component removal the worker drops solid blobs (component
  larger than 0.15 % of the image, fill ratio against its convex hull > 0.85 and holding a disc of
  radius >= max(6 px, 2 % of the shorter side); ridges are thin so they never qualify). A second filter
  drops border-touching components only when they are solid by the same test, or tiny (< 0.05 % of the
  image) and farther than 3 % of the shorter side from the main ridge region; thin elongated components
  that run off the border (the finger usually covers the whole sensor) always stay. Safety net: if both
  filters together would remove more than 5 % of the ink, the border filter is skipped for that image.
  The manifest records `quality.cleanup: {solidRemovedRatio, borderRemovedRatio, borderFilterSkipped}`.
- **`process`** writes `base.png` (square, 2048 px, ridges black on white, grayscale PNG) and version
  `v0` (`NATURAL`, offset 0, zoom 1, no erase; coverage is reported but not enforced). Response adds
  `version` and `base` to the usual `outputs`, `artwork: {aspectRatio, hull}`, `quality`:
  `version: {versionId, shape, offsetX, offsetY, zoom, erasedRatio, ridgeWidthRatio, minEngraveSizeMm, coverage}`,
  `base: {key, sha256, sizeBytes, widthPx, heightPx}`.
- **`GET /v1/fingerprint/presets`**: `{ defaultShape: "NATURAL", shapes: [{code, name, description, sampleSvg}] }`
  (Tự nhiên, Khung tròn, Trái tim, Vuông bo góc; sample SVGs are outline icons only).
- **`POST /v1/fingerprint/render-crop`** `{sourceId, outputPrefix, versionId (^[a-z0-9-]{1,40}$), shape,
  offsetX (-1..1), offsetY (-1..1), zoom (1..3), eraseStrokes? [{radius 0.005..0.1, points [[x,y] in 0..1]
  1..200}] max 50, context?}`. Missing `base.png` is `404 OBJECT_NOT_FOUND`. Erase strokes (white discs and
  lines in normalized base coordinates) are applied first; `erasedRatio` = erased ink pixels / ink pixels
  before, over 0.15 is `422 ERASE_AREA_TOO_LARGE`. The frame window has side (shorter side / zoom) and is
  centred on the base centre moved by the offset (offset 1 = half the shorter side), then masked by the shape
  (NATURAL = ellipse fitted to the ridge region of the window, CIRCLE, HEART, SQUARE with 12 % corner radius)
  and fitted to the 1024 canvas. `coverage` = frame area covered by dilated ink / frame area, under 0.70 is
  `422 FRAME_COVERAGE_TOO_LOW`. `ridgeWidthRatio` = 90th percentile stroke width (px) / canvas px;
  `minEngraveSizeMm` = max(3.0, ceil to 0.1 of 0.07 / ridgeWidthRatio). Zooming in makes ridges wider on the
  canvas, so `minEngraveSizeMm` goes down as zoom goes up. Response: `{status, processorVersion, outputs
  {vectorSvg, alphaPng, manifest}, artwork, version, base}`. The same `versionId` overwrites only that version.
- **`POST /v1/fingerprint/select-version`** `{sourceId, outputPrefix, versionId, context?}`: unknown version
  is `422 VERSION_NOT_FOUND`. Deletes the files of every other version **and `base.png`** (the accepted
  vector is final, so the base is no longer needed and render-crop afterwards is `404`), rewrites the
  manifest with only that version, `currentVersionId` and `"accepted": true` (no `base`). Idempotent.
  Response `{status, processorVersion, outputs, artwork, version, deleted: [keys]}`.
- **Manifest**: `{kind, processorVersion, request, quality, base, versions: {id: {versionId, shape, offsetX,
  offsetY, zoom, erasedRatio, ridgeWidthRatio, minEngraveSizeMm, coverage, eraseStrokes, hull, vectorSvg,
  alphaPng, createdAt}}, currentVersionId, outputs (current version files), artwork (current hull), context,
  accepted?}`.

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
  processing/              fingerprint.py, fingerprint_design.py, soundwave.py, wavecode.py, wavecode_styles.py: pipeline xử lý ảnh/audio
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
