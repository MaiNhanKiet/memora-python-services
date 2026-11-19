import json

import cv2
import numpy as np
from conftest import PREFIX, SOURCE_ID, needs_potrace

from app import config
from app.fingerprint import rescale_potrace_svg

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
    data = r.json()
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
    assert r.json()["failureCode"] == "IMAGE_UNREADABLE"


def test_blank_image_has_no_region_422(client, auth, store):
    ok, png = cv2.imencode(".png", np.full((300, 300), 200, dtype=np.uint8))
    assert ok
    store[RAW] = (png.tobytes(), "image/png")
    r = client.post("/v1/fingerprint/process", json=BODY, headers=auth)
    assert r.status_code == 422
    assert r.json()["failureCode"] == "FINGERPRINT_REGION_NOT_FOUND"


def test_missing_potrace_is_503(client, auth, store, monkeypatch):
    monkeypatch.setenv("POTRACE_BINARY", "potrace-does-not-exist")
    config.get_settings.cache_clear()
    ok, bmp = cv2.imencode(".bmp", synthetic_fingerprint())
    assert ok
    store[RAW] = (bmp.tobytes(), "image/bmp")
    r = client.post("/v1/fingerprint/process", json=BODY, headers=auth)
    assert r.status_code == 503
    assert r.json()["failureCode"] == "TOOL_UNAVAILABLE"
