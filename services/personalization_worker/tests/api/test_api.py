import re

import pytest
from conftest import PREFIX, SOURCE_ID

from app.core.config import ConfigError, load_settings
from app.storage import s3 as storage

LEVELS = [3, 5, 6, 4, 2, 1, 4, 6, 7, 5, 3, 2, 4, 5, 6, 3, 2, 1]
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
ENDPOINTS = [
    "/v1/fingerprint/process",
    "/v1/soundwave/process",
    "/v1/soundwave/render-wavecode",
]


def test_health_needs_no_secret(client, monkeypatch):
    monkeypatch.setattr(storage, "check", lambda: True)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"statusCode", "message", "data"}
    assert body["statusCode"] == 200 and body["message"] == "OK"
    assert body["data"]["status"] == "UP"
    assert set(body["data"]) == {"status", "potrace", "ffmpeg", "storage"}


def assert_error(r, status, code, path):
    assert r.status_code == status
    body = r.json()
    assert set(body) == {"statusCode", "code", "message", "path", "timestamp"}
    assert body["statusCode"] == status and body["code"] == code and body["path"] == path
    assert ISO_UTC.match(body["timestamp"])


@pytest.mark.parametrize("path", ENDPOINTS)
def test_missing_or_wrong_secret_is_401(client, path):
    for headers in ({}, {"X-Memora-Worker-Secret": "wrong"}):
        r = client.post(path, json={}, headers=headers)
        assert r.status_code == 401
        assert_error(r, 401, "UNAUTHORIZED", path)
        assert r.json()["message"] == "Invalid or missing worker secret."


@pytest.mark.parametrize("path", ENDPOINTS)
def test_bad_body_is_400(client, auth, path):
    r = client.post(path, json={"sourceId": "not-a-uuid"}, headers=auth)
    assert_error(r, 400, "INVALID_REQUEST", path)


def test_invalid_json_is_400(client, auth):
    r = client.post("/v1/fingerprint/process", content=b"{nope", headers=auth)
    assert r.status_code == 400


def test_prefix_must_end_with_slash_and_no_traversal(client, auth):
    body = {"sourceId": SOURCE_ID, "rawKey": "a/b", "outputPrefix": "personalization/x"}
    assert client.post("/v1/fingerprint/process", json=body, headers=auth).status_code == 400
    body = {"sourceId": SOURCE_ID, "rawKey": "../b", "outputPrefix": PREFIX}
    assert client.post("/v1/fingerprint/process", json=body, headers=auth).status_code == 400


def test_render_rejects_bad_levels(client, auth):
    for levels in (LEVELS[:17], [*LEVELS[:17], 8], [*LEVELS[:17], -1]):
        body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": levels}
        r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
        assert r.status_code == 400


def test_soundwave_segment_duration_must_be_3000(client, auth):
    body = {
        "sourceId": SOURCE_ID,
        "rawKey": "k",
        "outputPrefix": PREFIX,
        "contentType": "audio/wav",
        "declaredDurationMs": 5000,
        "segmentStartMs": 0,
        "segmentDurationMs": 2000,
    }
    assert client.post("/v1/soundwave/process", json=body, headers=auth).status_code == 400


def test_missing_raw_is_404(client, auth, store):
    body = {"sourceId": SOURCE_ID, "rawKey": "personalization/none", "outputPrefix": PREFIX}
    r = client.post("/v1/fingerprint/process", json=body, headers=auth)
    assert_error(r, 404, "OBJECT_NOT_FOUND", "/v1/fingerprint/process")


def test_render_wavecode_response_shape(client, auth, store):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS}
    r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
    assert r.status_code == 200, r.text
    envelope = r.json()
    assert set(envelope) == {"statusCode", "message", "data"}
    assert envelope["statusCode"] == 200 and envelope["message"] == "Wavecode rendered"
    data = envelope["data"]
    assert set(data) == {"status", "processorVersion", "outputs", "wavecode"}
    assert data["status"] == "READY"
    assert set(data["outputs"]) == {"vectorSvg", "alphaPng", "manifest"}
    assert data["outputs"]["vectorSvg"]["key"] == PREFIX + "wavecode.svg"
    assert data["outputs"]["alphaPng"]["widthPx"] == 2048
    assert data["outputs"]["alphaPng"]["heightPx"] == 512
    assert set(data["outputs"]["vectorSvg"]) == {"key", "sha256", "sizeBytes", "contentType"}
    wc = data["wavecode"]
    assert wc["scheme"] == "MEMORA_WAVECODE_v1"
    assert wc["levels"] == LEVELS
    assert wc["checkBars"] == [5, 5]
    assert wc["nudgeCandidates"] == []
    assert set(store) == {
        PREFIX + n for n in ("wavecode.svg", "wavecode_alpha.png", "manifest.json")
    }
    assert store[PREFIX + "wavecode.svg"][1] == "image/svg+xml"


def test_config_validation():
    with pytest.raises(ConfigError):
        load_settings({})
    base = {
        "WORKER_SHARED_SECRET": "x" * 16,
        "S3_ENDPOINT": "http://h:1",
        "S3_REGION": "r",
        "S3_BUCKET": "b",
        "S3_ACCESS_KEY_ID": "a",
        "S3_SECRET_ACCESS_KEY": "s",
    }
    assert load_settings(base).potrace_binary == "potrace"
    with pytest.raises(ConfigError):
        load_settings({**base, "WORKER_SHARED_SECRET": "short"})
    with pytest.raises(ConfigError):
        load_settings({**base, "S3_ENDPOINT": "garage:3900"})


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/nope")
    assert_error(r, 404, "NOT_FOUND", "/nope")


def test_wrong_method_uses_error_envelope(client):
    r = client.get("/v1/fingerprint/process")
    assert_error(r, 405, "METHOD_NOT_ALLOWED", "/v1/fingerprint/process")
    assert "POST" in r.headers["allow"]


def test_unexpected_error_hides_details(client, auth, monkeypatch):
    from app.processing import fingerprint

    def boom(_):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(fingerprint, "run", boom)
    body = {"sourceId": SOURCE_ID, "rawKey": PREFIX + "raw", "outputPrefix": PREFIX}
    r = client.post("/v1/fingerprint/process", json=body, headers=auth)
    assert_error(r, 500, "INTERNAL_ERROR", "/v1/fingerprint/process")
    assert "secret detail" not in r.text


def test_request_id_is_echoed_only_in_header(client):
    r = client.get("/health", headers={"X-Request-Id": "src-1_a.b:c"})
    assert r.headers["x-request-id"] == "src-1_a.b:c"
    assert "src-1_a.b:c" not in r.text
    r = client.get("/nope", headers={"X-Request-Id": "abc-2"})
    assert r.status_code == 404 and r.headers["x-request-id"] == "abc-2"


@pytest.mark.parametrize("value", ["a b", "x" * 101, "bad/slash"])
def test_invalid_request_id_is_ignored(client, value):
    r = client.get("/health", headers={"X-Request-Id": value})
    assert r.status_code == 200 and "x-request-id" not in r.headers


def test_keys_must_live_under_personalization_and_be_short(client, auth):
    path = "/v1/fingerprint/process"
    bad = [
        {"rawKey": "other/raw", "outputPrefix": PREFIX},
        {"rawKey": PREFIX + "raw", "outputPrefix": "other/out/"},
        {"rawKey": "personalization/" + "a" * 1024, "outputPrefix": PREFIX},
    ]
    for item in bad:
        r = client.post(path, json={"sourceId": SOURCE_ID, **item}, headers=auth)
        assert_error(r, 400, "INVALID_REQUEST", path)


def _sw(**over):
    return {
        "sourceId": SOURCE_ID,
        "rawKey": PREFIX + "raw",
        "outputPrefix": PREFIX,
        "contentType": "audio/wav",
        "declaredDurationMs": 5000,
        "segmentStartMs": 0,
        "segmentDurationMs": 3000,
        **over,
    }


@pytest.mark.parametrize(
    "over",
    [
        {"contentType": "audio/ogg"},
        {"contentType": "image/png"},
        {"declaredDurationMs": 2999},
        {"declaredDurationMs": 30001},
        {"segmentStartMs": 27001},
        {"segmentStartMs": -1},
    ],
)
def test_soundwave_field_limits(client, auth, over):
    r = client.post("/v1/soundwave/process", json=_sw(**over), headers=auth)
    assert_error(r, 400, "INVALID_REQUEST", "/v1/soundwave/process")
    assert next(iter(over)) in r.json()["message"]


def test_docs_are_disabled_by_default(client):
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert_error(client.get(path), 404, "NOT_FOUND", path)


def _enable_docs(monkeypatch):
    from app.core import config

    monkeypatch.setenv("WORKER_DOCS_ENABLED", "true")
    config.get_settings.cache_clear()


def test_docs_enabled_serves_swagger_and_schema(client, monkeypatch):
    _enable_docs(monkeypatch)
    page = client.get("/docs")
    assert page.status_code == 200 and "swagger-ui" in page.text.lower()
    assert client.get("/redoc").status_code == 404
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    doc = schema.json()
    assert {
        "/v1/fingerprint/process",
        "/v1/soundwave/process",
        "/v1/soundwave/render-wavecode",
        "/health",
    } <= set(doc["paths"])
    assert "/docs" not in doc["paths"]
    scheme = doc["components"]["securitySchemes"]["WorkerSecret"]
    assert scheme == {
        "type": "apiKey",
        "in": "header",
        "name": "X-Memora-Worker-Secret",
        "description": scheme["description"],
    }
    post = doc["paths"]["/v1/fingerprint/process"]["post"]
    assert post["security"] == [{"WorkerSecret": []}]
    assert {"200", "400", "401", "404", "422", "500", "503"} <= set(post["responses"])
    assert "security" not in doc["paths"]["/health"]["get"]
    ok = post["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert "Envelope" in ok and "FingerprintResponse" in ok


@pytest.mark.parametrize("path", ENDPOINTS)
def test_wrong_secret_beats_bad_body(client, path):
    r = client.post(path, json={"sourceId": "nope"}, headers={"X-Memora-Worker-Secret": "wrong"})
    assert_error(r, 401, "UNAUTHORIZED", path)
    r = client.post(path, content=b"{nope")
    assert_error(r, 401, "UNAUTHORIZED", path)


def test_good_secret_bad_body_lists_fields(client, auth):
    path = "/v1/soundwave/render-wavecode"
    r = client.post(path, json={"sourceId": "nope"}, headers=auth)
    assert_error(r, 400, "INVALID_REQUEST", path)
    assert r.json()["message"] == "Invalid request body: levels, outputPrefix, sourceId"
