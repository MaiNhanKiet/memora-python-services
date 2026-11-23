"""Audio -> 3 s segment -> MEMORA_WAVECODE_v1 SVG/PNG, plus re-render from explicit levels."""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import tempfile
import wave
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..core.config import get_settings
from ..core.errors import (
    audio_duration_invalid,
    audio_unreadable,
    segment_out_of_range,
    tool_unavailable,
)
from ..core.version import PROCESSOR_VERSION
from ..schemas import (
    OutputFile,
    PngOutputFile,
    RenderOutputs,
    RenderResponse,
    RenderWavecodeRequest,
    SoundwaveOutputs,
    SoundwaveRequest,
    SoundwaveResponse,
    Wavecode,
)
from ..storage import s3 as storage
from . import wavecode

logger = logging.getLogger("personalization.soundwave")

MIN_DURATION_MS = 3000
MAX_DURATION_MS = 30000
DURATION_TOLERANCE_MS = 250
SEGMENT_MS = 3000
WINDOWS = wavecode.DATA_BARS
FFMPEG_TIMEOUT_S = 60
# Bound decoding of oversized inputs: anything longer than this fails the 30 s check anyway.
FFMPEG_MAX_DECODE_S = 35


def _load_wav_stdlib(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width == 1:
        audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError("unsupported sample width")
    if channels > 1:
        audio = audio[: len(audio) // channels * channels].reshape(-1, channels).mean(axis=1)
    return audio.astype(np.float32), int(rate)


def _decode_with_ffmpeg(path: Path, workdir: Path, ffmpeg_binary: str) -> tuple[np.ndarray, int]:
    exe = shutil.which(ffmpeg_binary)
    if not exe:
        raise tool_unavailable("ffmpeg")
    out = workdir / "decoded.wav"
    cmd = [
        exe,
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-t",
        str(FFMPEG_MAX_DECODE_S),
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise tool_unavailable("ffmpeg") from exc
    except OSError as exc:
        raise tool_unavailable("ffmpeg") from exc
    if result.returncode != 0 or not out.is_file():
        raise audio_unreadable()
    try:
        return _load_wav_stdlib(out)
    except (wave.Error, ValueError, EOFError) as exc:
        raise audio_unreadable() from exc


def decode_audio(data: bytes, workdir: Path, ffmpeg_binary: str) -> tuple[np.ndarray, int]:
    """Decode to mono float32 in [-1, 1]. PCM WAV via stdlib, everything else via FFmpeg."""
    src = workdir / "raw.bin"
    src.write_bytes(data)
    try:
        samples, rate = _load_wav_stdlib(src)
    except (wave.Error, ValueError, EOFError):
        samples, rate = _decode_with_ffmpeg(src, workdir, ffmpeg_binary)
    if rate <= 0 or samples.size == 0:
        raise audio_unreadable()
    return samples, rate


def window_rms(segment: np.ndarray, windows: int = WINDOWS) -> np.ndarray:
    edges = np.linspace(0, len(segment), windows + 1).astype(int)
    out = np.zeros(windows, dtype=np.float64)
    for i in range(windows):
        chunk = segment[edges[i] : edges[i + 1]].astype(np.float64)
        out[i] = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
    return out


def segment_to_wav(segment: np.ndarray, rate: int) -> bytes:
    pcm = np.clip(np.rint(segment * 32767.0), -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def analyze(
    data: bytes, declared_ms: int, start_ms: int, ffmpeg_binary: str
) -> tuple[int, bytes, list[int], np.ndarray]:
    """Return (measuredDurationMs, segment wav bytes, levels, scaled envelope)."""
    with tempfile.TemporaryDirectory(prefix="sw-") as tmp:
        samples, rate = decode_audio(data, Path(tmp), ffmpeg_binary)
    measured = round(len(samples) / rate * 1000)
    if not MIN_DURATION_MS <= measured <= MAX_DURATION_MS:
        raise audio_duration_invalid(
            f"Measured duration {measured} ms is outside {MIN_DURATION_MS}..{MAX_DURATION_MS} ms."
        )
    if abs(measured - declared_ms) > DURATION_TOLERANCE_MS:
        raise audio_duration_invalid(
            f"Measured duration {measured} ms differs from declared {declared_ms} ms "
            f"by more than {DURATION_TOLERANCE_MS} ms."
        )
    if start_ms + SEGMENT_MS > measured:
        raise segment_out_of_range(
            f"Segment {start_ms}+{SEGMENT_MS} ms exceeds measured duration {measured} ms."
        )
    first = round(start_ms * rate / 1000)
    segment = samples[first : first + round(SEGMENT_MS * rate / 1000)]
    levels, scaled = wavecode.quantize_envelope(window_rms(segment))
    return measured, segment_to_wav(segment, rate), levels, scaled


def _wavecode_model(levels: list[int], nudges: list[list[int]]) -> Wavecode:
    check, bars = wavecode.build_bars(levels)
    return Wavecode(levels=levels, checkBars=check, bars=bars, nudgeCandidates=nudges)


def _png_model(obj: storage.StoredObject) -> PngOutputFile:
    return PngOutputFile(**asdict(obj), widthPx=wavecode.PNG_WIDTH, heightPx=wavecode.PNG_HEIGHT)


def _upload_wavecode(
    prefix: str, bars: list[int]
) -> tuple[storage.StoredObject, storage.StoredObject]:
    svg_bytes = wavecode.render_svg(bars).encode("utf-8")
    svg = storage.put_object(prefix + "wavecode.svg", svg_bytes, "image/svg+xml")
    png = storage.put_object(prefix + "wavecode_alpha.png", wavecode.render_png(bars), "image/png")
    return svg, png


def run(req: SoundwaveRequest) -> SoundwaveResponse:
    settings = get_settings()
    logger.info("soundwave start", extra={"ctx": {"sourceId": req.sourceId, "rawKey": req.rawKey}})
    raw = storage.get_object(req.rawKey)
    measured, wav_bytes, levels, scaled = analyze(
        raw, req.declaredDurationMs, req.segmentStartMs, settings.ffmpeg_binary
    )
    wc = _wavecode_model(levels, wavecode.nudge_candidates(levels, scaled))
    svg, png = _upload_wavecode(req.outputPrefix, wc.bars)
    audio = storage.put_object(req.outputPrefix + "audio_segment.wav", wav_bytes, "audio/wav")
    manifest_doc = {
        "kind": "SOUNDWAVE",
        "processorVersion": PROCESSOR_VERSION,
        "request": req.model_dump(),
        "measuredDurationMs": measured,
        "outputs": {
            "vectorSvg": asdict(svg),
            "alphaPng": {
                **asdict(png),
                "widthPx": wavecode.PNG_WIDTH,
                "heightPx": wavecode.PNG_HEIGHT,
            },
            "audioSegment": asdict(audio),
        },
        "wavecode": wc.model_dump(),
    }
    manifest = storage.put_object(
        req.outputPrefix + "manifest.json",
        json.dumps(manifest_doc, indent=2).encode("utf-8"),
        "application/json",
    )
    return SoundwaveResponse(
        processorVersion=PROCESSOR_VERSION,
        measuredDurationMs=measured,
        outputs=SoundwaveOutputs(
            vectorSvg=OutputFile(**asdict(svg)),
            alphaPng=_png_model(png),
            audioSegment=OutputFile(**asdict(audio)),
            manifest=OutputFile(**asdict(manifest)),
        ),
        wavecode=wc,
    )


def render(req: RenderWavecodeRequest) -> RenderResponse:
    logger.info("render-wavecode start", extra={"ctx": {"sourceId": req.sourceId}})
    wc = _wavecode_model(list(req.levels), [])
    svg, png = _upload_wavecode(req.outputPrefix, wc.bars)

    # Keep the unchanged audio segment entry and measured duration of the earlier manifest.
    manifest_key = req.outputPrefix + "manifest.json"
    previous: dict = {}
    existing = storage.get_object_optional(manifest_key)
    if existing:
        try:
            loaded = json.loads(existing)
            previous = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            previous = {}
    outputs = {
        "vectorSvg": asdict(svg),
        "alphaPng": {**asdict(png), "widthPx": wavecode.PNG_WIDTH, "heightPx": wavecode.PNG_HEIGHT},
    }
    audio_entry = (previous.get("outputs") or {}).get("audioSegment")
    if audio_entry:
        outputs["audioSegment"] = audio_entry
    manifest_doc = {
        "kind": "SOUNDWAVE",
        "processorVersion": PROCESSOR_VERSION,
        "request": previous.get("request")
        or {"sourceId": req.sourceId, "outputPrefix": req.outputPrefix},
        "measuredDurationMs": previous.get("measuredDurationMs"),
        "outputs": outputs,
        "wavecode": wc.model_dump(),
        "renderedFromLevels": True,
    }
    manifest = storage.put_object(
        manifest_key, json.dumps(manifest_doc, indent=2).encode("utf-8"), "application/json"
    )
    return RenderResponse(
        processorVersion=PROCESSOR_VERSION,
        outputs=RenderOutputs(
            vectorSvg=OutputFile(**asdict(svg)),
            alphaPng=_png_model(png),
            manifest=OutputFile(**asdict(manifest)),
        ),
        wavecode=wc,
    )
