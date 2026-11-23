"""Audio -> 3 s segment -> MEMORA_WAVECODE_v1 SVG/PNG, plus re-render from explicit levels."""

from __future__ import annotations

import hashlib
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
    style_not_generated,
    tool_unavailable,
)
from ..core.version import PROCESSOR_VERSION
from ..schemas import (
    OutputFile,
    PngOutputFile,
    PresetsResponse,
    PresetStyle,
    RenderOutputs,
    RenderResponse,
    RenderWavecodeRequest,
    SelectResponse,
    SelectWavecodeStyleRequest,
    SoundwaveOutputs,
    SoundwaveRequest,
    SoundwaveResponse,
    StyleFiles,
    Wavecode,
)
from ..storage import s3 as storage
from . import wavecode
from .wavecode_styles import DEFAULT_STYLE, STYLES

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


def _wavecode_model(
    levels: list[int],
    nudges: list[list[int]],
    style: str,
    styles: dict[str, dict] | None = None,
) -> Wavecode:
    check, bars = wavecode.build_bars(levels)
    return Wavecode(
        style=style,
        levels=levels,
        checkBars=check,
        bars=bars,
        nudgeCandidates=nudges,
        styles=styles or {},
    )


def _manifest_wavecode(wc: Wavecode) -> dict:
    """manifest.json `wavecode`: scheme, levels, checkBars, bars, style, styles (no nudges)."""
    return wc.model_dump(exclude={"nudgeCandidates"})


def presets() -> PresetsResponse:
    return PresetsResponse(
        defaultStyle=DEFAULT_STYLE,
        styles=[
            PresetStyle(
                code=st.code,
                name=st.name,
                description=st.description,
                decoder=st.decoder,
                sampleSvg=wavecode.sample_svg(st.code),
            )
            for st in STYLES.values()
        ],
    )


def style_keys(prefix: str, style: str) -> tuple[str, str]:
    """(svg key, png key) of one style under the output prefix."""
    return f"{prefix}wavecode-{style.lower()}.svg", f"{prefix}wavecode-{style.lower()}_alpha.png"


def _style_entry(svg: storage.StoredObject, png: storage.StoredObject) -> dict:
    return {
        "vectorSvg": asdict(svg),
        "alphaPng": {
            **asdict(png),
            "widthPx": wavecode.PNG_WIDTH,
            "heightPx": wavecode.PNG_HEIGHT,
        },
    }


def _upload_wavecode(prefix: str, bars: list[int], style: str) -> dict:
    svg_key, png_key = style_keys(prefix, style)
    svg = storage.put_object(
        svg_key, wavecode.render_svg(bars, style=style).encode("utf-8"), "image/svg+xml"
    )
    png = storage.put_object(png_key, wavecode.render_png(bars, style), "image/png")
    return _style_entry(svg, png)


def _put_manifest(key: str, doc: dict) -> storage.StoredObject:
    return storage.put_object(key, json.dumps(doc, indent=2).encode("utf-8"), "application/json")


def _load_manifest(key: str) -> dict:
    existing = storage.get_object_optional(key)
    if not existing:
        return {}
    try:
        loaded = json.loads(existing)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _entry_exists(entry: object) -> bool:
    try:
        return all(storage.object_exists(entry[k]["key"]) for k in ("vectorSvg", "alphaPng"))
    except (KeyError, TypeError):
        return False


def run(req: SoundwaveRequest) -> SoundwaveResponse:
    settings = get_settings()
    logger.info("soundwave start", extra={"ctx": {"sourceId": req.sourceId, "rawKey": req.rawKey}})
    raw = storage.get_object(req.rawKey)
    measured, wav_bytes, levels, scaled = analyze(
        raw, req.declaredDurationMs, req.segmentStartMs, settings.ffmpeg_binary
    )
    wc = _wavecode_model(levels, wavecode.nudge_candidates(levels, scaled), req.style)
    entry = _upload_wavecode(req.outputPrefix, wc.bars, req.style)
    wc.styles = {req.style: StyleFiles(**entry)}
    audio = storage.put_object(req.outputPrefix + "audio_segment.wav", wav_bytes, "audio/wav")
    manifest_doc = {
        "kind": "SOUNDWAVE",
        "processorVersion": PROCESSOR_VERSION,
        "request": req.model_dump(exclude={"context"}),
        "measuredDurationMs": measured,
        "outputs": {**entry, "audioSegment": asdict(audio)},
        "wavecode": _manifest_wavecode(wc),
    }
    if req.context is not None:
        manifest_doc["context"] = req.context.model_dump(exclude_none=True)
    manifest = _put_manifest(req.outputPrefix + "manifest.json", manifest_doc)
    return SoundwaveResponse(
        processorVersion=PROCESSOR_VERSION,
        measuredDurationMs=measured,
        outputs=SoundwaveOutputs(
            vectorSvg=OutputFile(**entry["vectorSvg"]),
            alphaPng=PngOutputFile(**entry["alphaPng"]),
            audioSegment=OutputFile(**asdict(audio)),
            manifest=OutputFile(**asdict(manifest)),
        ),
        wavecode=wc,
    )


def _render_outputs(entry: dict, manifest: OutputFile) -> RenderOutputs:
    return RenderOutputs(
        vectorSvg=OutputFile(**entry["vectorSvg"]),
        alphaPng=PngOutputFile(**entry["alphaPng"]),
        manifest=manifest,
    )


def _manifest_outputs(entry: dict, previous: dict) -> dict:
    outputs = dict(entry)
    audio_entry = (previous.get("outputs") or {}).get("audioSegment")
    if audio_entry:
        outputs["audioSegment"] = audio_entry
    return outputs


def render(req: RenderWavecodeRequest) -> RenderResponse:
    logger.info("render-wavecode start", extra={"ctx": {"sourceId": req.sourceId}})
    manifest_key = req.outputPrefix + "manifest.json"
    previous = _load_manifest(manifest_key)
    prev_wc = previous.get("wavecode") or {}
    prev_styles = prev_wc.get("styles") or {}
    previous_style = prev_wc.get("style")
    style = req.style or (previous_style if previous_style in STYLES else DEFAULT_STYLE)
    levels = list(req.levels)
    same_levels = prev_wc.get("levels") == levels
    context = req.context.model_dump(exclude_none=True) if req.context else previous.get("context")

    if same_levels and style in prev_styles and _entry_exists(prev_styles[style]):
        entry = prev_styles[style]  # reuse: nothing is rendered or written for the style files
        wc = _wavecode_model(levels, [], style, prev_styles)
        if previous_style == style and context == previous.get("context"):
            raw = storage.get_object(manifest_key)
            manifest = OutputFile(
                key=manifest_key,
                sha256=hashlib.sha256(raw).hexdigest(),
                sizeBytes=len(raw),
                contentType="application/json",
            )
        else:
            doc = {**previous, "outputs": _manifest_outputs(entry, previous)}
            doc["wavecode"] = _manifest_wavecode(wc)
            if context:
                doc["context"] = context
            manifest = OutputFile(**asdict(_put_manifest(manifest_key, doc)))
        return RenderResponse(
            processorVersion=PROCESSOR_VERSION,
            outputs=_render_outputs(entry, manifest),
            wavecode=wc,
            reused=True,
        )

    # Same levels: add this style next to the others. New levels: other styles are stale.
    styles = dict(prev_styles) if same_levels else {}
    entry = _upload_wavecode(req.outputPrefix, wavecode.build_bars(levels)[1], style)
    styles[style] = entry
    wc = _wavecode_model(levels, [], style, styles)
    manifest_doc = {
        "kind": "SOUNDWAVE",
        "processorVersion": PROCESSOR_VERSION,
        "request": previous.get("request")
        or {"sourceId": req.sourceId, "outputPrefix": req.outputPrefix},
        "measuredDurationMs": previous.get("measuredDurationMs"),
        "outputs": _manifest_outputs(entry, previous),
        "wavecode": _manifest_wavecode(wc),
        "renderedFromLevels": True,
    }
    if context:
        manifest_doc["context"] = context
    manifest = _put_manifest(manifest_key, manifest_doc)
    return RenderResponse(
        processorVersion=PROCESSOR_VERSION,
        outputs=_render_outputs(entry, OutputFile(**asdict(manifest))),
        wavecode=wc,
        reused=False,
    )


def select_style(req: SelectWavecodeStyleRequest) -> SelectResponse:
    """Accept one style: delete every other style's files and keep only it in the manifest."""
    logger.info("select-wavecode-style start", extra={"ctx": {"sourceId": req.sourceId}})
    manifest_key = req.outputPrefix + "manifest.json"
    previous = _load_manifest_strict(manifest_key)
    prev_wc = previous.get("wavecode") or {}
    styles = prev_wc.get("styles") or {}
    entry = styles.get(req.style)
    if not isinstance(entry, dict) or not _entry_exists(entry):
        raise style_not_generated(req.style)

    # Other styles: those in the manifest plus the canonical names (leftovers of earlier runs).
    doomed: list[str | None] = []
    for code, other in styles.items():
        if code != req.style and isinstance(other, dict):
            doomed += [(other.get(k) or {}).get("key") for k in ("vectorSvg", "alphaPng")]
    for code in STYLES:
        if code != req.style:
            doomed += list(style_keys(req.outputPrefix, code))
    keep = {entry["vectorSvg"]["key"], entry["alphaPng"]["key"]}
    deleted: list[str] = []
    for key in dict.fromkeys(k for k in doomed if k and k not in keep):
        if storage.object_exists(key):
            storage.delete_object(key)
            deleted.append(key)

    wc = _wavecode_model(list(prev_wc.get("levels") or []), [], req.style, {req.style: entry})
    doc = {**previous, "outputs": _manifest_outputs(entry, previous), "accepted": True}
    doc["wavecode"] = _manifest_wavecode(wc)
    context = req.context.model_dump(exclude_none=True) if req.context else previous.get("context")
    if context:
        doc["context"] = context
    manifest = _put_manifest(manifest_key, doc)
    return SelectResponse(
        processorVersion=PROCESSOR_VERSION,
        outputs=_render_outputs(entry, OutputFile(**asdict(manifest))),
        wavecode=wc,
        deleted=deleted,
    )


def _load_manifest_strict(key: str) -> dict:
    """Like _load_manifest but a missing manifest is OBJECT_NOT_FOUND."""
    raw = storage.get_object(key)
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
