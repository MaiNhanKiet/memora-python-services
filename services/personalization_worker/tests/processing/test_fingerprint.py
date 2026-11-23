import json

import cv2
import numpy as np
from conftest import PREFIX, SOURCE_ID, needs_potrace

from app.core import config
from app.processing.fingerprint import rescale_potrace_svg

RAW = "personalization/fingerprint/raw/s1/a1"
BODY = {"sourceId": SOURCE_ID, "rawKey": RAW, "outputPrefix": PREFIX}


def synthetic_fingerprint() -> np.ndarray:
    img = np.full((480, 360), 235, dtype=np.uint8)
    for i in range(1, 20):
        cv2.ellipse(img, (180, 240), (8 * i, 11 * i), 0, 0, 360, 40, 3)
    return img


def test_rescale_potrace_svg_unitless_viewbox():
    raw = (
        '<svg viewBox="0 0 1024.000000 1024.000000"><g transform="translate(0,1024) '
        'scale(0.100000,-0.100000)"><path d="M0 0 L10 0\nL10 10 z"/></g></svg>'
    )
    svg = rescale_potrace_svg(raw)
    assert 'viewBox="0 0 1000 1000"' in svg
    assert '<path d="M0 0 L10 0 L10 10 z"/>' in svg
    assert "scale(0.09765625,-0.09765625)" in svg


@needs_potrace
def test_pipeline_on_synthetic_image(client, auth, store):
    ok, bmp = cv2.imencode(".bmp", synthetic_fingerprint())
    assert ok
    store[RAW] = (bmp.tobytes(), "image/bmp")

    r = client.post("/v1/fingerprint/process", json=BODY, headers=auth)
    assert r.status_code == 200, r.text
    envelope = r.json()
    assert envelope["statusCode"] == 200 and envelope["message"] == "Fingerprint processed"
    data = envelope["data"]
    assert data["status"] == "READY"
    assert set(data) == {"status", "processorVersion", "outputs", "artwork", "quality"}
    out = data["outputs"]
    assert out["vectorSvg"]["key"] == PREFIX + "fingerprint.svg"
    assert out["alphaPng"]["key"] == PREFIX + "fingerprint_alpha.png"
    assert out["manifest"]["key"] == PREFIX + "manifest.json"
    assert out["alphaPng"]["widthPx"] == out["alphaPng"]["heightPx"] == 1024

    svg = store[PREFIX + "fingerprint.svg"][0].decode()
    assert 'viewBox="0 0 1000 1000"' in svg and "<path" in svg
    assert store[PREFIX + "fingerprint.svg"][1] == "image/svg+xml"

    png = cv2.imdecode(
        np.frombuffer(store[PREFIX + "fingerprint_alpha.png"][0], np.uint8), cv2.IMREAD_UNCHANGED
    )
    assert png.shape == (1024, 1024, 4)
    assert png[0, 0, 3] == 0 and (png[:, :, 3] == 255).any()

    hull = data["artwork"]["hull"]
    assert data["artwork"]["aspectRatio"] == 1.0
    assert 3 <= len(hull) <= 32
    assert all(0.0 <= v <= 1.0 for point in hull for v in point)
    assert 0 <= data["quality"]["score"] <= 100

    manifest = json.loads(store[PREFIX + "manifest.json"][0])
    assert manifest["artwork"]["hull"] == hull
    assert manifest["outputs"]["vectorSvg"]["sha256"] == out["vectorSvg"]["sha256"]


def test_unreadable_image_is_422(client, auth, store):
    store[RAW] = (b"this is not an image", "image/bmp")
    r = client.post("/v1/fingerprint/process", json=BODY, headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "IMAGE_UNREADABLE"


def test_blank_image_has_no_region_422(client, auth, store):
    ok, png = cv2.imencode(".png", np.full((300, 300), 200, dtype=np.uint8))
    assert ok
    store[RAW] = (png.tobytes(), "image/png")
    r = client.post("/v1/fingerprint/process", json=BODY, headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "FINGERPRINT_REGION_NOT_FOUND"


def test_missing_potrace_is_503(client, auth, store, monkeypatch):
    monkeypatch.setenv("POTRACE_BINARY", "potrace-does-not-exist")
    config.get_settings.cache_clear()
    ok, bmp = cv2.imencode(".bmp", synthetic_fingerprint())
    assert ok
    store[RAW] = (bmp.tobytes(), "image/bmp")
    r = client.post("/v1/fingerprint/process", json=BODY, headers=auth)
    assert r.status_code == 503
    assert r.json()["code"] == "TOOL_UNAVAILABLE"


@needs_potrace
def test_context_is_copied_into_manifest(client, auth, store):
    ok, bmp = cv2.imencode(".bmp", synthetic_fingerprint())
    assert ok
    store[RAW] = (bmp.tobytes(), "image/bmp")
    context = {
        "orderId": "7d0a9c52-1b3e-4f6a-8c21-5e9d4b7a1f30",
        "sessionId": "b2c4e6a8-0f1d-4a3b-9c5e-7d8f1a2b3c4d",
        "attemptNo": 2,
        "ringItemIds": ["c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"],
    }
    r = client.post("/v1/fingerprint/process", json={**BODY, "context": context}, headers=auth)
    assert r.status_code == 200, r.text
    assert json.loads(store[PREFIX + "manifest.json"][0])["context"] == context
