import pytest
from conftest import PREFIX, SOURCE_ID

from app import storage
from app.config import ConfigError, load_settings

LEVELS = [3, 5, 6, 4, 2, 1, 4, 6, 7, 5, 3, 2, 4, 5, 6, 3, 2, 1]
ENDPOINTS = [
    "/v1/fingerprint/process",
    "/v1/soundwave/process",
    "/v1/soundwave/render-wavecode",
]


def test_health_needs_no_secret(client, monkeypatch):
    monkeypatch.setattr(storage, "check", lambda: True)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert set(r.json()) == {"status", "potrace", "ffmpeg", "storage"}


@pytest.mark.parametrize("path", ENDPOINTS)
def test_missing_or_wrong_secret_is_401(client, path):
    for headers in ({}, {"X-Memora-Worker-Secret": "wrong"}):
        r = client.post(path, json={}, headers=headers)
        assert r.status_code == 401
        assert r.json() == {
            "status": "FAILED",
            "failureCode": "UNAUTHORIZED",
            "message": "Invalid or missing worker secret.",
        }


@pytest.mark.parametrize("path", ENDPOINTS)
def test_bad_body_is_400(client, auth, path):
    r = client.post(path, json={"sourceId": "not-a-uuid"}, headers=auth)
    assert r.status_code == 400
    assert r.json()["failureCode"] == "INVALID_REQUEST"
    assert r.json()["status"] == "FAILED"


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
    assert r.status_code == 404
    assert r.json()["failureCode"] == "OBJECT_NOT_FOUND"


def test_render_wavecode_response_shape(client, auth, store):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS}
    r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
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
