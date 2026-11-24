"""Fingerprint artwork design: cleanup, base.png, versions, render-crop, select-version."""

import json

import cv2
import numpy as np
import pytest
from conftest import PREFIX, SOURCE_ID, needs_potrace

from app.processing import fingerprint as fp

RAW = "personalization/fingerprint/raw/s1/a1"
PROCESS = "/v1/fingerprint/process"
CROP = "/v1/fingerprint/render-crop"
SELECT = "/v1/fingerprint/select-version"
PRESETS = "/v1/fingerprint/presets"


def synthetic_fingerprint() -> np.ndarray:
    img = np.full((480, 360), 235, dtype=np.uint8)
    for i in range(1, 20):
        cv2.ellipse(img, (180, 240), (8 * i, 11 * i), 0, 0, 360, 40, 3)
    return img


def vkeys(version_id):
    base = f"{PREFIX}versions/{version_id}/"
    return base + "fingerprint.svg", base + "fingerprint_alpha.png"


def manifest(store):
    return json.loads(store[PREFIX + "manifest.json"][0])


def alpha_ink(store, version_id):
    png = cv2.imdecode(
        np.frombuffer(store[vkeys(version_id)[1]][0], np.uint8), cv2.IMREAD_UNCHANGED
    )
    return int((png[:, :, 3] == 255).sum())


@pytest.fixture
def processed(client, auth, store):
    ok, bmp = cv2.imencode(".bmp", synthetic_fingerprint())
    assert ok
    store[RAW] = (bmp.tobytes(), "image/bmp")
    body = {"sourceId": SOURCE_ID, "rawKey": RAW, "outputPrefix": PREFIX}
    r = client.post(PROCESS, json=body, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def crop(client, auth, **over):
    body = {
        "sourceId": SOURCE_ID,
        "outputPrefix": PREFIX,
        "versionId": "v1",
        "shape": "CIRCLE",
        "offsetX": 0,
        "offsetY": 0,
        "zoom": 2,
        **over,
    }
    return client.post(CROP, json=body, headers=auth)


def select(client, auth, version_id):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "versionId": version_id}
    return client.post(SELECT, json=body, headers=auth)


# ---------------------------------------------------------------- cleanup (no potrace needed)
def test_solid_blob_is_dropped_and_ridges_stay():
    img = synthetic_fingerprint()
    binary = fp.ensure_black_ridges_white_bg(
        cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 4)
    )
    binary = fp.remove_small_components(binary)
    ridges_before = int((binary < 128).sum())
    with_blob = binary.copy()
    cv2.rectangle(with_blob, (0, 0), (60, 60), 0, -1)  # solid black corner blob
    cleaned = fp.drop_solid_blobs(with_blob)
    assert int((cleaned[:60, :60] < 128).sum()) == 0  # blob gone
    assert abs(int((cleaned < 128).sum()) - ridges_before) < 0.02 * ridges_before  # ridges kept


def test_thin_straight_ridge_is_not_a_blob():
    img = np.full((300, 300), 255, dtype=np.uint8)
    cv2.line(img, (20, 150), (280, 150), 0, 5)
    assert int((fp.drop_solid_blobs(img) < 128).sum()) == int((img < 128).sum())


def edge_print() -> np.ndarray:
    """Parallel diagonal ridges that run off the left and bottom edges, like a full-sensor print."""
    img = np.full((288, 256), 255, dtype=np.uint8)
    for k in range(-300, 600, 11):
        cv2.line(img, (0, k), (k + 300, 288 + 40), 0, 4)
    cv2.rectangle(img, (0, 0), (255, 6), 255, -1)
    return img


def test_ridges_running_off_the_border_are_kept():
    img = edge_print()
    before = int((img < 128).sum())
    out = fp.drop_border_strays(img)
    assert int((out < 128).sum()) >= 0.98 * before
    assert int((fp.drop_solid_blobs(img) < 128).sum()) >= 0.98 * before


def test_thin_elongated_border_component_stays_even_when_far():
    img = np.full((300, 300), 255, dtype=np.uint8)
    cv2.ellipse(img, (150, 150), (60, 80), 0, 0, 360, 0, 5)
    cv2.line(img, (0, 295), (60, 295), 0, 4)  # thin, long, touches the border, far from the print
    assert int((fp.drop_border_strays(img)[290:, :70] < 128).sum()) > 0


def test_tiny_far_border_speck_is_dropped():
    img = np.full((300, 300), 255, dtype=np.uint8)
    cv2.ellipse(img, (150, 150), (60, 80), 0, 0, 360, 0, 5)
    cv2.circle(img, (2, 295), 3, 0, -1)  # tiny speck on the border, far from the print
    out = fp.drop_border_strays(img)
    assert int((out[285:, :12] < 128).sum()) == 0
    assert int((out[60:240, 80:220] < 128).sum()) == int((img[60:240, 80:220] < 128).sum())


def test_solid_corner_blob_touching_border_is_removed():
    img = edge_print()
    img[:] = 255
    cv2.ellipse(img, (128, 144), (90, 110), 0, 0, 360, 0, 4)
    cv2.rectangle(img, (0, 220), (50, 287), 0, -1)  # solid dark blob in the bottom-left corner
    out = fp.drop_border_strays(img)
    assert int((out[225:, :45] < 128).sum()) == 0
    assert int((fp.drop_solid_blobs(img)[225:, :45] < 128).sum()) == 0


def test_cleanup_safety_net_skips_border_filter(monkeypatch):
    img = synthetic_fingerprint()
    plain, info = fp.clean_binary_info(img)
    assert info["borderFilterSkipped"] is False and info["borderRemovedRatio"] == 0.0

    def greedy(binary):  # a border filter gone wrong: wipes a tenth of the print
        out = binary.copy()
        out[200:280, 120:240] = 255
        return out

    monkeypatch.setattr(fp, "drop_border_strays", greedy)
    safe, info = fp.clean_binary_info(img)
    assert info["borderFilterSkipped"] is True and info["borderRemovedRatio"] == 0.0
    assert safe.shape == plain.shape
    assert int((safe < 128).sum()) == int((plain < 128).sum())


@needs_potrace
def test_cleanup_info_is_in_manifest(processed, store):
    cleanup = manifest(store)["quality"]["cleanup"]
    assert set(cleanup) == {"solidRemovedRatio", "borderRemovedRatio", "borderFilterSkipped"}
    assert "cleanup" not in processed["quality"]


def test_clean_binary_loses_corner_blob_keeps_print():
    img = synthetic_fingerprint()
    plain = fp.clean_binary(img)
    blobbed = img.copy()
    cv2.rectangle(blobbed, (0, 0), (60, 60), 0, -1)
    cleaned = fp.clean_binary(blobbed)
    assert cleaned.shape == plain.shape  # the blob did not stretch the crop
    assert abs(int((cleaned < 128).sum()) - int((plain < 128).sum())) < 0.02 * int(
        (plain < 128).sum()
    )


# ---------------------------------------------------------------- presets
def test_presets_requires_secret(client):
    r = client.get(PRESETS)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED"


def test_presets_lists_four_frames(client, auth):
    r = client.get(PRESETS, headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["defaultShape"] == "NATURAL"
    assert [s["code"] for s in data["shapes"]] == ["NATURAL", "CIRCLE", "HEART", "SQUARE"]
    assert [s["name"] for s in data["shapes"]] == [
        "Tự nhiên",
        "Khung tròn",
        "Trái tim",
        "Vuông bo góc",
    ]
    for shape in data["shapes"]:
        assert set(shape) == {"code", "name", "description", "sampleSvg"}
        assert shape["description"] and shape["sampleSvg"].startswith("<svg")


# ---------------------------------------------------------------- process
@needs_potrace
def test_process_writes_base_and_v0(processed, store):
    data = processed
    assert set(store) - {RAW} == {PREFIX + "base.png", *vkeys("v0"), PREFIX + "manifest.json"}
    assert data["outputs"]["vectorSvg"]["key"] == vkeys("v0")[0]
    assert data["outputs"]["alphaPng"]["key"] == vkeys("v0")[1]
    assert data["artwork"]["aspectRatio"] == 1.0
    base = data["base"]
    assert set(base) == {"key", "sha256", "sizeBytes", "widthPx", "heightPx"}
    assert base["key"] == PREFIX + "base.png" and max(base["widthPx"], base["heightPx"]) == 2048
    img = cv2.imdecode(np.frombuffer(store[PREFIX + "base.png"][0], np.uint8), cv2.IMREAD_UNCHANGED)
    assert img.shape[:2] == (base["heightPx"], base["widthPx"])
    assert set(np.unique(img)) <= {0, 255} and img[0, 0] == 255 and (img == 0).any()
    version = data["version"]
    assert set(version) == {
        "versionId",
        "shape",
        "offsetX",
        "offsetY",
        "zoom",
        "erasedRatio",
        "ridgeWidthRatio",
        "minEngraveSizeMm",
        "coverage",
    }
    assert (version["versionId"], version["shape"]) == ("v0", "NATURAL")
    assert (version["offsetX"], version["offsetY"], version["zoom"]) == (0, 0, 1)
    assert version["erasedRatio"] == 0 and version["minEngraveSizeMm"] >= 3.0
    doc = manifest(store)
    assert doc["currentVersionId"] == "v0" and list(doc["versions"]) == ["v0"]
    assert doc["base"]["key"] == PREFIX + "base.png"
    assert doc["outputs"]["vectorSvg"]["key"] == vkeys("v0")[0]


# ---------------------------------------------------------------- render-crop
@needs_potrace
@pytest.mark.parametrize("shape", ["NATURAL", "CIRCLE", "HEART", "SQUARE"])
def test_render_crop_each_shape(client, auth, store, processed, shape):
    r = crop(client, auth, shape=shape)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert set(data) == {"status", "processorVersion", "outputs", "artwork", "version", "base"}
    assert data["version"]["shape"] == shape and data["version"]["coverage"] >= 0.7
    assert data["outputs"]["vectorSvg"]["key"] == vkeys("v1")[0]
    assert set(vkeys("v1")) <= set(store) and set(vkeys("v0")) <= set(store)
    assert 'viewBox="0 0 1000 1000"' in store[vkeys("v1")[0]][0].decode()
    assert 3 <= len(data["artwork"]["hull"]) <= 32
    doc = manifest(store)
    assert doc["currentVersionId"] == "v1" and set(doc["versions"]) == {"v0", "v1"}
    assert doc["versions"]["v1"]["shape"] == shape and doc["versions"]["v1"]["eraseStrokes"] == []


@needs_potrace
def test_offset_and_zoom_change_the_crop(client, auth, store, processed):
    def sha(**over):
        r = crop(client, auth, **over)
        assert r.status_code == 200, r.text
        return r.json()["data"]["outputs"]["alphaPng"]["sha256"]

    base = sha(zoom=2)
    assert sha(zoom=2, offsetX=0.2) != base
    assert sha(zoom=2, offsetY=-0.2) != base
    assert sha(zoom=2.5) != base


@needs_potrace
def test_ridge_width_grows_and_min_size_shrinks_with_zoom(client, auth, store, processed):
    low = crop(client, auth, zoom=1.5, shape="SQUARE", versionId="v1").json()["data"]["version"]
    high = crop(client, auth, zoom=3, shape="SQUARE", versionId="v2").json()["data"]["version"]
    assert high["ridgeWidthRatio"] > low["ridgeWidthRatio"]
    assert high["minEngraveSizeMm"] <= low["minEngraveSizeMm"]
    assert high["minEngraveSizeMm"] >= 3.0


@needs_potrace
def test_erase_within_cap_removes_ink(client, auth, store, processed):
    plain = crop(client, auth, versionId="v1")
    assert plain.status_code == 200
    base = cv2.imdecode(
        np.frombuffer(store[PREFIX + "base.png"][0], np.uint8), cv2.IMREAD_GRAYSCALE
    )
    ys, xs = np.nonzero(base < 128)
    mid = len(xs) // 2
    point = [float(xs[mid]) / base.shape[1], float(ys[mid]) / base.shape[0]]
    stroke = {"radius": 0.03, "points": [point, [min(1.0, point[0] + 0.02), point[1]]]}
    erased = crop(client, auth, versionId="v2", eraseStrokes=[stroke])
    assert erased.status_code == 200, erased.text
    ratio = erased.json()["data"]["version"]["erasedRatio"]
    assert 0 < ratio <= 0.15
    assert alpha_ink(store, "v2") < alpha_ink(store, "v1")
    assert manifest(store)["versions"]["v2"]["eraseStrokes"] == [stroke]


@needs_potrace
def test_erase_over_cap_is_422(client, auth, store, processed):
    points = [[x / 10, y / 10] for x in range(1, 10) for y in range(1, 10)][:80]
    strokes = [{"radius": 0.1, "points": points}]
    r = crop(client, auth, eraseStrokes=strokes)
    assert r.status_code == 422 and r.json()["code"] == "ERASE_AREA_TOO_LARGE"
    assert not set(vkeys("v1")) & set(store)


@needs_potrace
def test_coverage_too_low_is_422(client, auth, store, processed):
    r = crop(client, auth, shape="CIRCLE", zoom=1, offsetX=1, offsetY=1)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "FRAME_COVERAGE_TOO_LOW" and r.json()["path"] == CROP
    assert not set(vkeys("v1")) & set(store)


@needs_potrace
def test_same_version_id_overwrites_only_that_version(client, auth, store, processed):
    v0_before = {k: store[k][0] for k in vkeys("v0")}
    assert crop(client, auth, shape="CIRCLE").status_code == 200
    first = store[vkeys("v1")[0]][0]
    assert crop(client, auth, shape="SQUARE").status_code == 200
    assert store[vkeys("v1")[0]][0] != first
    assert {k: store[k][0] for k in vkeys("v0")} == v0_before
    doc = manifest(store)
    assert set(doc["versions"]) == {"v0", "v1"} and doc["versions"]["v1"]["shape"] == "SQUARE"


def test_render_crop_without_base_is_404(client, auth, store):
    r = crop(client, auth)
    assert r.status_code == 404 and r.json()["code"] == "OBJECT_NOT_FOUND"


def test_render_crop_requires_secret(client):
    r = client.post(CROP, json={})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "over",
    [
        {"shape": "STAR"},
        {"versionId": "V1"},
        {"versionId": "a" * 41},
        {"versionId": "v_1"},
        {"offsetX": 1.1},
        {"offsetY": -1.1},
        {"zoom": 0.9},
        {"zoom": 3.1},
        {"eraseStrokes": [{"radius": 0.001, "points": [[0.5, 0.5]]}]},
        {"eraseStrokes": [{"radius": 0.2, "points": [[0.5, 0.5]]}]},
        {"eraseStrokes": [{"radius": 0.05, "points": []}]},
        {"eraseStrokes": [{"radius": 0.05, "points": [[1.5, 0.5]]}]},
        {"eraseStrokes": [{"radius": 0.05, "points": [[0.5, 0.5]] * 201}]},
        {"eraseStrokes": [{"radius": 0.05, "points": [[0.5, 0.5]]}] * 51},
        {"outputPrefix": "personalization/x"},
    ],
)
def test_render_crop_invalid_body_is_400(client, auth, over):
    r = crop(client, auth, **over)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------- select-version
@needs_potrace
def test_select_deletes_others_and_base_and_is_idempotent(client, auth, store, processed):
    assert crop(client, auth, versionId="v1").status_code == 200
    assert crop(client, auth, versionId="v2", shape="SQUARE").status_code == 200
    r = select(client, auth, "v1")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Fingerprint version selected"
    data = r.json()["data"]
    assert set(data) == {"status", "processorVersion", "outputs", "artwork", "version", "deleted"}
    assert data["version"]["versionId"] == "v1"
    assert data["outputs"]["vectorSvg"]["key"] == vkeys("v1")[0]
    assert set(data["deleted"]) == {PREFIX + "base.png", *vkeys("v0"), *vkeys("v2")}
    assert set(store) - {RAW} == {*vkeys("v1"), PREFIX + "manifest.json"}
    doc = manifest(store)
    assert doc["accepted"] is True and doc["currentVersionId"] == "v1"
    assert list(doc["versions"]) == ["v1"] and "base" not in doc
    before = doc
    again = select(client, auth, "v1")
    assert again.status_code == 200 and again.json()["data"]["deleted"] == []
    assert manifest(store) == before


@needs_potrace
def test_select_repeat_ignores_unlisted_files(client, auth, store, processed):
    assert select(client, auth, "v0").status_code == 200
    store[vkeys("v7")[0]] = (b"<svg/>", "image/svg+xml")
    # leftover of an earlier run that is no longer in the manifest is not guessed; v-ids in the
    # manifest are the contract, so only listed versions are cleaned
    again = select(client, auth, "v0")
    assert again.status_code == 200 and again.json()["data"]["deleted"] == []


@needs_potrace
def test_select_unknown_version_is_422(client, auth, store, processed):
    r = select(client, auth, "v9")
    assert r.status_code == 422
    assert r.json()["code"] == "VERSION_NOT_FOUND" and r.json()["path"] == SELECT
    assert PREFIX + "base.png" in store


def test_select_without_manifest_is_404(client, auth, store):
    r = select(client, auth, "v0")
    assert r.status_code == 404 and r.json()["code"] == "OBJECT_NOT_FOUND"


def test_select_requires_secret(client):
    r = client.post(SELECT, json={})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "over", [{"versionId": "V1"}, {"versionId": ""}, {"outputPrefix": "other/"}, {"sourceId": "x"}]
)
def test_select_invalid_body_is_400(client, auth, over):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "versionId": "v1", **over}
    r = client.post(SELECT, json=body, headers=auth)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
