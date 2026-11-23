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
    assert set(data) == {"status", "processorVersion", "outputs", "wavecode", "reused"}
    assert data["status"] == "READY"
    assert set(data["outputs"]) == {"vectorSvg", "alphaPng", "manifest"}
    assert data["outputs"]["vectorSvg"]["key"] == PREFIX + "wavecode-classic.svg"
    assert data["outputs"]["alphaPng"]["widthPx"] == 2125
    assert data["outputs"]["alphaPng"]["heightPx"] == 500
    assert set(data["outputs"]["vectorSvg"]) == {"key", "sha256", "sizeBytes", "contentType"}
    wc = data["wavecode"]
    assert wc["scheme"] == "MEMORA_WAVECODE_v1"
    assert wc["levels"] == LEVELS
    assert wc["checkBars"] == [5, 5]
    assert wc["nudgeCandidates"] == []
    assert set(store) == {
        PREFIX + n for n in ("wavecode-classic.svg", "wavecode-classic_alpha.png", "manifest.json")
    }
    assert store[PREFIX + "wavecode-classic.svg"][1] == "image/svg+xml"


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


CONTEXT = {
    "orderId": "7d0a9c52-1b3e-4f6a-8c21-5e9d4b7a1f30",
    "ringItemIds": ["c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"],
}


def test_render_context_goes_into_manifest_and_is_kept(client, auth, store):
    import json

    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS, "context": CONTEXT}
    assert client.post("/v1/soundwave/render-wavecode", json=body, headers=auth).status_code == 200
    manifest = json.loads(store[PREFIX + "manifest.json"][0])
    assert manifest["context"] == CONTEXT
    body.pop("context")
    assert client.post("/v1/soundwave/render-wavecode", json=body, headers=auth).status_code == 200
    assert json.loads(store[PREFIX + "manifest.json"][0])["context"] == CONTEXT


def test_absent_context_is_omitted_from_manifest(client, auth, store):
    import json

    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS}
    assert client.post("/v1/soundwave/render-wavecode", json=body, headers=auth).status_code == 200
    assert "context" not in json.loads(store[PREFIX + "manifest.json"][0])


@pytest.mark.parametrize(
    "context",
    [
        {**CONTEXT, "extra": 1},
        {**CONTEXT, "orderId": "nope"},
        {**CONTEXT, "attemptNo": 4},
        {**CONTEXT, "attemptNo": 0},
        {**CONTEXT, "ringItemIds": [CONTEXT["ringItemIds"][0]] * 3},
        {**CONTEXT, "sessionId": "x"},
        {"sessionId": CONTEXT["orderId"]},
    ],
)
def test_invalid_context_is_400(client, auth, store, context):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS, "context": context}
    r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
    assert_error(r, 400, "INVALID_REQUEST", "/v1/soundwave/render-wavecode")


def _manifest(store):
    import json

    return json.loads(store[PREFIX + "manifest.json"][0])


@pytest.mark.parametrize("style", ["CLASSIC", "HAIRLINE", "OSCILLOSCOPE"])
def test_render_style_in_response_manifest_and_svg(client, auth, store, style):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS, "style": style}
    r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["wavecode"]["style"] == style
    assert _manifest(store)["wavecode"]["style"] == style
    assert f'data-style="{style}"' in store[PREFIX + f"wavecode-{style.lower()}.svg"][0].decode()


def test_render_default_style_is_classic(client, auth, store):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS}
    r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
    assert r.json()["data"]["wavecode"]["style"] == "CLASSIC"


def test_render_keeps_previous_style_when_omitted(client, auth, store):
    body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS, "style": "HAIRLINE"}
    assert client.post("/v1/soundwave/render-wavecode", json=body, headers=auth).status_code == 200
    body.pop("style")
    r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
    assert r.json()["data"]["wavecode"]["style"] == "HAIRLINE"
    assert _manifest(store)["wavecode"]["style"] == "HAIRLINE"
    body["style"] = "OSCILLOSCOPE"
    r = client.post("/v1/soundwave/render-wavecode", json=body, headers=auth)
    assert r.json()["data"]["wavecode"]["style"] == "OSCILLOSCOPE"


@pytest.mark.parametrize("path", ["/v1/soundwave/render-wavecode", "/v1/soundwave/process"])
def test_invalid_style_is_400(client, auth, store, path):
    if path.endswith("process"):
        body = _sw(style="NEON")
    else:
        body = {"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": LEVELS, "style": "NEON"}
    assert_error(client.post(path, json=body, headers=auth), 400, "INVALID_REQUEST", path)


def test_presets_requires_secret(client):
    r = client.get("/v1/soundwave/presets")
    assert_error(r, 401, "UNAUTHORIZED", "/v1/soundwave/presets")


def test_presets_lists_three_styles_with_sample_svg(client, auth):
    r = client.get("/v1/soundwave/presets", headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["defaultStyle"] == "CLASSIC"
    styles = {s["code"]: s for s in data["styles"]}
    assert [s["code"] for s in data["styles"]] == ["CLASSIC", "HAIRLINE", "OSCILLOSCOPE"]
    assert [s["name"] for s in data["styles"]] == ["Cổ điển", "Nét mảnh", "Sóng liền"]
    assert styles["CLASSIC"]["decoder"] == styles["HAIRLINE"]["decoder"] == "BAR_HEIGHT"
    assert styles["OSCILLOSCOPE"]["decoder"] == "PEAK_HEIGHT"
    for code, s in styles.items():
        assert set(s) == {"code", "name", "description", "decoder", "sampleSvg"}
        assert s["description"]
        assert s["sampleSvg"].startswith("<?xml") and f'data-style="{code}"' in s["sampleSvg"]
        assert 'data-levels="3,5,6,4,2,1,4,6,7,5,3,2,4,5,6,3,2,1"' in s["sampleSvg"]
