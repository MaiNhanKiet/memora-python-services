import io
import json
import wave

import numpy as np
from conftest import PREFIX, SOURCE_ID, needs_ffmpeg

from app import config

RAW = f"personalization/sources/{SOURCE_ID}/raw"
RATE = 16000


def make_wav(seconds: float, amp_fn=None) -> bytes:
    t = np.arange(int(seconds * RATE)) / RATE
    amp = amp_fn(t) if amp_fn else 0.5
    pcm = (np.sin(2 * np.pi * 440 * t) * amp * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def body(**over):
    base = {
        "sourceId": SOURCE_ID,
        "rawKey": RAW,
        "outputPrefix": PREFIX,
        "contentType": "audio/wav",
        "declaredDurationMs": 12400,
        "segmentStartMs": 4200,
        "segmentDurationMs": 3000,
    }
    return {**base, **over}


def ramp(t):
    return 0.05 + 0.9 * (t / t[-1])


def test_soundwave_pipeline(client, auth, store):
    store[RAW] = (make_wav(12.4, ramp), "audio/wav")
    r = client.post("/v1/soundwave/process", json=body(), headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["measuredDurationMs"] == 12400
    assert set(data["outputs"]) == {"vectorSvg", "alphaPng", "audioSegment", "manifest"}
    wc = data["wavecode"]
    assert wc["scheme"] == "MEMORA_WAVECODE_v1"
    assert len(wc["levels"]) == 18 and len(wc["bars"]) == 23 and len(wc["checkBars"]) == 2
    assert wc["levels"] == sorted(wc["levels"])  # amplitude ramp rises
    assert max(wc["levels"]) == 7
    assert wc["bars"][0] == wc["bars"][11] == wc["bars"][22] == 7
    assert wc["bars"][1:10] == wc["levels"][:9] and wc["bars"][12:21] == wc["levels"][9:]
    assert 1 <= len(wc["nudgeCandidates"]) <= 8
    assert data["outputs"]["audioSegment"]["contentType"] == "audio/wav"

    with wave.open(io.BytesIO(store[PREFIX + "audio_segment.wav"][0])) as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2
        assert wf.getnframes() == 3 * RATE
    manifest = json.loads(store[PREFIX + "manifest.json"][0])
    assert manifest["wavecode"]["levels"] == wc["levels"]


def test_render_keeps_audio_segment_in_manifest(client, auth, store):
    store[RAW] = (make_wav(12.4, ramp), "audio/wav")
    first = client.post("/v1/soundwave/process", json=body(), headers=auth).json()
    levels = first["wavecode"]["nudgeCandidates"][0]
    r = client.post(
        "/v1/soundwave/render-wavecode",
        json={"sourceId": SOURCE_ID, "outputPrefix": PREFIX, "levels": levels},
        headers=auth,
    )
    assert r.status_code == 200
    manifest = json.loads(store[PREFIX + "manifest.json"][0])
    assert manifest["outputs"]["audioSegment"]["key"] == PREFIX + "audio_segment.wav"
    assert manifest["wavecode"]["levels"] == levels


def test_silent_audio_all_zero_levels(client, auth, store):
    store[RAW] = (make_wav(5.0, lambda t: 0.0 * t), "audio/wav")
    r = client.post(
        "/v1/soundwave/process",
        json=body(declaredDurationMs=5000, segmentStartMs=0),
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["wavecode"]["levels"] == [0] * 18


def test_duration_too_short_and_too_long(client, auth, store):
    store[RAW] = (make_wav(2.0), "audio/wav")
    r = client.post("/v1/soundwave/process", json=body(declaredDurationMs=2000), headers=auth)
    assert r.status_code == 422 and r.json()["failureCode"] == "AUDIO_DURATION_INVALID"
    store[RAW] = (make_wav(31.0), "audio/wav")
    r = client.post("/v1/soundwave/process", json=body(declaredDurationMs=31000), headers=auth)
    assert r.status_code == 422 and r.json()["failureCode"] == "AUDIO_DURATION_INVALID"


def test_declared_duration_mismatch(client, auth, store):
    store[RAW] = (make_wav(10.0), "audio/wav")
    ok = client.post("/v1/soundwave/process", json=body(declaredDurationMs=10200), headers=auth)
    assert ok.status_code == 200
    bad = client.post("/v1/soundwave/process", json=body(declaredDurationMs=10300), headers=auth)
    assert bad.status_code == 422 and bad.json()["failureCode"] == "AUDIO_DURATION_INVALID"


def test_segment_out_of_range(client, auth, store):
    store[RAW] = (make_wav(10.0), "audio/wav")
    r = client.post(
        "/v1/soundwave/process",
        json=body(declaredDurationMs=10000, segmentStartMs=7001),
        headers=auth,
    )
    assert r.status_code == 422 and r.json()["failureCode"] == "SEGMENT_OUT_OF_RANGE"
    r = client.post(
        "/v1/soundwave/process",
        json=body(declaredDurationMs=10000, segmentStartMs=7000),
        headers=auth,
    )
    assert r.status_code == 200


@needs_ffmpeg
def test_undecodable_audio_is_422(client, auth, store):
    store[RAW] = (b"definitely not audio" * 50, "audio/mpeg")
    r = client.post("/v1/soundwave/process", json=body(), headers=auth)
    assert r.status_code == 422 and r.json()["failureCode"] == "AUDIO_UNREADABLE"


def test_missing_ffmpeg_is_503(client, auth, store, monkeypatch):
    monkeypatch.setenv("FFMPEG_BINARY", "ffmpeg-does-not-exist")
    config.get_settings.cache_clear()
    store[RAW] = (b"not a wav so ffmpeg is needed", "audio/mpeg")
    r = client.post("/v1/soundwave/process", json=body(), headers=auth)
    assert r.status_code == 503 and r.json()["failureCode"] == "TOOL_UNAVAILABLE"
