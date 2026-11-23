"""Per-style wavecode files, reuse, and select-wavecode-style."""

import json

import pytest
from conftest import PREFIX, SOURCE_ID

from app.storage import s3 as storage

LEVELS = [3, 5, 6, 4, 2, 1, 4, 6, 7, 5, 3, 2, 4, 5, 6, 3, 2, 1]
OTHER_LEVELS = [1, 2, 3, 4, 5, 6, 7, 6, 5, 4, 3, 2, 1, 2, 3, 4, 5, 6]
RENDER = "/v1/soundwave/render-wavecode"
SELECT = "/v1/soundwave/select-wavecode-style"


def svg_key(style):
    return f"{PREFIX}wavecode-{style.lower()}.svg"


def png_key(style):
    return f"{PREFIX}wavecode-{style.lower()}_alpha.png"


def render(client, auth, style=None, levels=LEVELS):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": levels}
    if style:
        body["style"] = style
    r = client.post(RENDER, json=body, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def select(client, auth, style):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "style": style}
    return client.post(SELECT, json=body, headers=auth)


def manifest(store):
    return json.loads(store[PREFIX + "manifest.json"][0])


@pytest.fixture
def puts(monkeypatch, store):
    """Keys written through storage.put_object, in order."""
    keys: list[str] = []
    original = storage.put_object

    def counting(key, data, content_type):
        keys.append(key)
        return original(key, data, content_type)

    monkeypatch.setattr(storage, "put_object", counting)
    return keys


def test_render_writes_only_requested_style_files(client, auth, store):
    data = render(client, auth, "HAIRLINE")
    assert set(store) == {svg_key("HAIRLINE"), png_key("HAIRLINE"), PREFIX + "manifest.json"}
    assert data["outputs"]["vectorSvg"]["key"] == svg_key("HAIRLINE")
    assert data["outputs"]["alphaPng"]["key"] == png_key("HAIRLINE")
    assert data["reused"] is False
    assert list(data["wavecode"]["styles"]) == ["HAIRLINE"]
    entry = data["wavecode"]["styles"]["HAIRLINE"]
    assert set(entry) == {"vectorSvg", "alphaPng"}
    assert set(entry["vectorSvg"]) == {"key", "sha256", "sizeBytes", "contentType"}
    assert {"widthPx", "heightPx"} <= set(entry["alphaPng"])
    wc = manifest(store)["wavecode"]
    assert set(wc) == {"scheme", "levels", "checkBars", "bars", "style", "styles"}
    assert wc["style"] == "HAIRLINE" and wc["styles"] == {"HAIRLINE": entry}


def test_second_style_is_added_without_touching_first(client, auth, store, puts):
    render(client, auth, "CLASSIC")
    first = {k: store[k][0] for k in (svg_key("CLASSIC"), png_key("CLASSIC"))}
    puts.clear()
    data = render(client, auth, "OSCILLOSCOPE")
    assert data["reused"] is False
    assert set(puts) == {svg_key("OSCILLOSCOPE"), png_key("OSCILLOSCOPE"), PREFIX + "manifest.json"}
    assert {k: store[k][0] for k in first} == first
    assert set(data["wavecode"]["styles"]) == {"CLASSIC", "OSCILLOSCOPE"}
    assert manifest(store)["wavecode"]["style"] == "OSCILLOSCOPE"


def test_same_style_same_levels_is_reused(client, auth, store, puts):
    render(client, auth, "CLASSIC")
    render(client, auth, "HAIRLINE")
    puts.clear()
    data = render(client, auth, "CLASSIC")
    assert data["reused"] is True
    assert puts == [PREFIX + "manifest.json"]  # only the current style changes
    assert data["outputs"]["vectorSvg"]["key"] == svg_key("CLASSIC")
    assert manifest(store)["wavecode"]["style"] == "CLASSIC"
    assert set(data["wavecode"]["styles"]) == {"CLASSIC", "HAIRLINE"}
    puts.clear()
    again = render(client, auth, "CLASSIC")
    assert again["reused"] is True and puts == []  # nothing at all to write
    assert again["outputs"]["manifest"]["key"] == PREFIX + "manifest.json"


def test_omitted_style_reuses_current_style(client, auth, store, puts):
    render(client, auth, "HAIRLINE")
    puts.clear()
    data = render(client, auth)
    assert data["reused"] is True and puts == []
    assert data["wavecode"]["style"] == "HAIRLINE"


def test_missing_file_forces_rerender(client, auth, store, puts):
    render(client, auth, "CLASSIC")
    del store[png_key("CLASSIC")]
    puts.clear()
    data = render(client, auth, "CLASSIC")
    assert data["reused"] is False
    assert png_key("CLASSIC") in puts and png_key("CLASSIC") in store


def test_same_style_new_levels_resets_styles(client, auth, store):
    render(client, auth, "CLASSIC")
    render(client, auth, "HAIRLINE")
    data = render(client, auth, "HAIRLINE", OTHER_LEVELS)
    assert data["reused"] is False
    assert list(data["wavecode"]["styles"]) == ["HAIRLINE"]
    wc = manifest(store)["wavecode"]
    assert list(wc["styles"]) == ["HAIRLINE"] and wc["levels"] == OTHER_LEVELS


def test_select_deletes_others_and_rewrites_manifest(client, auth, store):
    for style in ("CLASSIC", "HAIRLINE", "OSCILLOSCOPE"):
        render(client, auth, style)
    r = select(client, auth, "HAIRLINE")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Wavecode style selected"
    data = r.json()["data"]
    assert set(data) == {"status", "processorVersion", "outputs", "wavecode", "deleted"}
    assert set(data["outputs"]) == {"vectorSvg", "alphaPng", "manifest"}
    assert data["outputs"]["vectorSvg"]["key"] == svg_key("HAIRLINE")
    assert set(data["deleted"]) == {
        svg_key("CLASSIC"),
        png_key("CLASSIC"),
        svg_key("OSCILLOSCOPE"),
        png_key("OSCILLOSCOPE"),
    }
    assert set(store) == {svg_key("HAIRLINE"), png_key("HAIRLINE"), PREFIX + "manifest.json"}
    doc = manifest(store)
    assert doc["accepted"] is True
    assert doc["wavecode"]["style"] == "HAIRLINE"
    assert list(doc["wavecode"]["styles"]) == ["HAIRLINE"]
    assert list(data["wavecode"]["styles"]) == ["HAIRLINE"]


def test_select_is_idempotent_and_cleans_leftovers(client, auth, store):
    render(client, auth, "CLASSIC")
    render(client, auth, "HAIRLINE")
    assert select(client, auth, "CLASSIC").status_code == 200
    store[svg_key("OSCILLOSCOPE")] = (b"<svg/>", "image/svg+xml")  # leftover of an earlier run
    before = manifest(store)
    r = select(client, auth, "CLASSIC")
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] == [svg_key("OSCILLOSCOPE")]
    assert manifest(store) == before
    assert set(store) == {svg_key("CLASSIC"), png_key("CLASSIC"), PREFIX + "manifest.json"}
    assert select(client, auth, "CLASSIC").json()["data"]["deleted"] == []


def test_select_missing_style_is_422(client, auth, store):
    render(client, auth, "CLASSIC")
    r = select(client, auth, "HAIRLINE")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "STYLE_NOT_GENERATED" and body["path"] == SELECT
    assert set(store) == {svg_key("CLASSIC"), png_key("CLASSIC"), PREFIX + "manifest.json"}


def test_select_with_missing_file_is_422(client, auth, store):
    render(client, auth, "CLASSIC")
    del store[svg_key("CLASSIC")]
    assert select(client, auth, "CLASSIC").json()["code"] == "STYLE_NOT_GENERATED"


def test_select_without_manifest_is_404(client, auth, store):
    r = select(client, auth, "CLASSIC")
    assert r.status_code == 404 and r.json()["code"] == "OBJECT_NOT_FOUND"


def test_select_requires_secret(client):
    r = client.post(
        SELECT, json={"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "style": "CLASSIC"}
    )
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    "over",
    [
        {"style": "NEON"},
        {"style": None},
        {"outputPrefix": "personalization/x"},
        {"outputPrefix": "other/x/"},
        {"sourceId": "nope"},
        {"context": {"orderId": "nope"}},
    ],
)
def test_select_invalid_body_is_400(client, auth, over):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "style": "CLASSIC", **over}
    body = {k: v for k, v in body.items() if v is not None}
    r = client.post(SELECT, json=body, headers=auth)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST"
