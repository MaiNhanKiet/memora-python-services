"""Fingerprint artwork design: cleaned base image, framed versions (crop + erase), acceptance.

Layout under `outputPrefix`: `base.png`, `versions/{versionId}/fingerprint.svg` +
`fingerprint_alpha.png`, `manifest.json`. Version `v0` (NATURAL, no offset, zoom 1) is created by
`process`; `render_crop` adds or overwrites one version; `select_version` keeps one version and
deletes everything else, including `base.png`.
"""

from __future__ import annotations

import json
import logging
import math
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..core.config import get_settings
from ..core.errors import (
    erase_area_too_large,
    fingerprint_region_not_found,
    frame_coverage_too_low,
    image_unreadable,
    version_not_found,
)
from ..core.version import PROCESSOR_VERSION
from ..schemas import (
    Artwork,
    BaseImage,
    FingerprintOutputs,
    FingerprintPresetsResponse,
    FingerprintRequest,
    FingerprintResponse,
    FrameShapePreset,
    OutputFile,
    PngOutputFile,
    Quality,
    RenderCropRequest,
    RenderCropResponse,
    SelectVersionRequest,
    SelectVersionResponse,
    Version,
)
from ..storage import s3 as storage
from . import fingerprint as fp

logger = logging.getLogger("personalization.fingerprint")

DEFAULT_SHAPE = "NATURAL"
DEFAULT_VERSION_ID = "v0"
MAX_ERASED_RATIO = 0.15
MIN_COVERAGE = 0.70
MIN_RIDGE_MM = 0.07
MIN_ENGRAVE_SIZE_MM = 3.0
SQUARE_RADIUS = 0.12
NATURAL_FIT = 1.06


@dataclass(frozen=True)
class Frame:
    code: str
    name: str
    description: str
    sample_svg: str


def _icon(shape: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="none" '
        f'stroke="#000" stroke-width="3" stroke-linejoin="round">{shape}</svg>'
    )


FRAMES: dict[str, Frame] = {
    f.code: f
    for f in (
        Frame(
            "NATURAL",
            "Tự nhiên",
            "Viền oval tự nhiên theo hình dạng vân tay, giữ trọn vẹn phần vân.",
            _icon('<ellipse cx="50" cy="50" rx="34" ry="44"/>'),
        ),
        Frame(
            "CIRCLE",
            "Khung tròn",
            "Cắt vân tay trong khung hình tròn, phù hợp mặt khắc nhỏ.",
            _icon('<circle cx="50" cy="50" r="42"/>'),
        ),
        Frame(
            "HEART",
            "Trái tim",
            "Cắt vân tay trong khung hình trái tim.",
            _icon(
                '<path d="M50 88 C8 58 8 24 31 17 C42 14 48 21 50 27 C52 21 58 14 69 17 '
                'C92 24 92 58 50 88 Z"/>'
            ),
        ),
        Frame(
            "SQUARE",
            "Vuông bo góc",
            "Cắt vân tay trong khung hình vuông bo tròn bốn góc.",
            _icon('<rect x="10" y="10" width="80" height="80" rx="12"/>'),
        ),
    )
}


def presets() -> FingerprintPresetsResponse:
    return FingerprintPresetsResponse(
        defaultShape=DEFAULT_SHAPE,
        shapes=[
            FrameShapePreset(
                code=f.code, name=f.name, description=f.description, sampleSvg=f.sample_svg
            )
            for f in FRAMES.values()
        ],
    )


# ---------------------------------------------------------------- image operations
def apply_erase(base: np.ndarray, strokes: list[Any]) -> tuple[np.ndarray, float]:
    """White discs and lines along each stroke (normalized coords). Returns (image, erasedRatio)."""
    ink_before = int(np.count_nonzero(base < 128))
    if ink_before == 0:
        raise fingerprint_region_not_found()
    if not strokes:
        return base, 0.0
    out = base.copy()
    h, w = out.shape[:2]
    side = min(h, w)
    for stroke in strokes:
        radius = max(1, round(stroke.radius * side))
        pts = [(round(x * (w - 1)), round(y * (h - 1))) for x, y in stroke.points]
        for p in pts:
            cv2.circle(out, p, radius, 255, -1)
        for a, b in zip(pts, pts[1:], strict=False):
            cv2.line(out, a, b, 255, 2 * radius)
    ink_after = int(np.count_nonzero(out < 128))
    return out, (ink_before - ink_after) / ink_before


def _heart_polygon(n: int) -> np.ndarray:
    t = np.radians(np.arange(360))
    x = 16 * np.sin(t) ** 3
    y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
    x = (x - x.min()) / (x.max() - x.min())
    y = 1.0 - (y - y.min()) / (y.max() - y.min())
    margin = 0.02 * n
    return np.stack([margin + x * (n - 2 * margin), margin + y * (n - 2 * margin)], axis=1)


def shape_mask(shape: str, n: int, ink: np.ndarray) -> np.ndarray:
    """255 inside the frame. NATURAL is an ellipse fitted to the ridge region of the crop."""
    mask = np.zeros((n, n), dtype=np.uint8)
    if shape == "CIRCLE":
        cv2.circle(mask, (n // 2, n // 2), round(0.49 * n), 255, -1)
    elif shape == "SQUARE":
        m, r = round(0.01 * n), round(SQUARE_RADIUS * n)
        lo, hi = m, n - 1 - m
        cv2.rectangle(mask, (lo + r, lo), (hi - r, hi), 255, -1)
        cv2.rectangle(mask, (lo, lo + r), (hi, hi - r), 255, -1)
        for cx, cy in ((lo + r, lo + r), (hi - r, lo + r), (lo + r, hi - r), (hi - r, hi - r)):
            cv2.circle(mask, (cx, cy), r, 255, -1)
    elif shape == "HEART":
        cv2.fillPoly(mask, [np.round(_heart_polygon(n)).astype(np.int32)], 255)
    else:  # NATURAL
        ys, xs = np.nonzero(ink)
        if xs.size == 0:
            cv2.ellipse(mask, (n // 2, n // 2), (n // 2, n // 2), 0, 0, 360, 255, -1)
        else:
            cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
            ax = max(4.0, (xs.max() - xs.min()) / 2 * NATURAL_FIT)
            ay = max(4.0, (ys.max() - ys.min()) / 2 * NATURAL_FIT)
            cv2.ellipse(mask, (round(cx), round(cy)), (round(ax), round(ay)), 0, 0, 360, 255, -1)
    return mask


def crop_frame(
    img: np.ndarray, offset_x: float, offset_y: float, zoom: float, n: int
) -> np.ndarray:
    """Square window of side (shorter side / zoom) around the centre moved by the offset.

    Offset 1 equals half the shorter side. Parts outside the image are white. The window is
    resampled to n x n and re-thresholded (ridges 0, background 255).
    """
    h, w = img.shape[:2]
    side = min(h, w)
    c = max(2, round(side / zoom))
    x0 = round(w / 2 + offset_x * side / 2 - c / 2)
    y0 = round(h / 2 + offset_y * side / 2 - c / 2)
    window = np.full((c, c), 255, dtype=np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(w, x0 + c), min(h, y0 + c)
    if sx1 > sx0 and sy1 > sy0:
        window[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = img[sy0:sy1, sx0:sx1]
    interp = cv2.INTER_AREA if c > n else cv2.INTER_CUBIC
    resized = cv2.resize(window, (n, n), interpolation=interp)
    return np.where(resized < 128, 0, 255).astype(np.uint8)


def ridge_width_px(ink: np.ndarray) -> float:
    """90th percentile of the local stroke width (2 x distance to background) in pixels."""
    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    values = dist[dist > 0]
    return float(np.percentile(values, 90)) * 2 if values.size else 0.0


def frame_coverage(ink: np.ndarray, mask: np.ndarray, width_px: float) -> float:
    """Share of the frame holding ridges; a dilated ink mask makes the gaps between ridges count."""
    area = int(np.count_nonzero(mask))
    if area == 0 or not ink.any():
        return 0.0
    radius = max(6, round(1.2 * width_px))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    covered = cv2.dilate(ink.astype(np.uint8), kernel) > 0
    return float(np.count_nonzero(covered & (mask > 0))) / area


def min_engrave_size_mm(ridge_width_ratio: float) -> float:
    if ridge_width_ratio <= 0:
        return MIN_ENGRAVE_SIZE_MM
    return max(MIN_ENGRAVE_SIZE_MM, math.ceil(MIN_RIDGE_MM / ridge_width_ratio * 10 - 1e-9) / 10)


@dataclass
class RenderedVersion:
    svg: bytes
    png: bytes
    hull: list[list[float]]
    meta: dict[str, Any] = field(default_factory=dict)


def render_version(
    base: np.ndarray,
    *,
    version_id: str,
    shape: str,
    offset_x: float,
    offset_y: float,
    zoom: float,
    strokes: list[Any],
    potrace_binary: str,
    enforce: bool,
) -> RenderedVersion:
    erased, erased_ratio = apply_erase(base, strokes)
    if enforce and erased_ratio > MAX_ERASED_RATIO:
        raise erase_area_too_large(erased_ratio)
    n = fp.CANVAS_SIZE
    canvas = crop_frame(erased, offset_x, offset_y, zoom, n)
    mask = shape_mask(shape, n, canvas < 128)
    canvas[mask == 0] = 255
    ink = canvas < 128
    if not ink.any():
        if enforce:
            raise frame_coverage_too_low(0.0)
        raise fingerprint_region_not_found()
    width_px = ridge_width_px(ink)
    coverage = frame_coverage(ink, mask, width_px)
    if enforce and coverage < MIN_COVERAGE:
        raise frame_coverage_too_low(coverage)
    ratio = width_px / n
    with tempfile.TemporaryDirectory(prefix="fp-") as tmp:
        svg = fp.vectorize(canvas, potrace_binary, Path(tmp))
    return RenderedVersion(
        svg=svg.encode("utf-8"),
        png=fp.alpha_png(canvas),
        hull=fp.compute_hull(canvas),
        meta={
            "versionId": version_id,
            "shape": shape,
            "offsetX": offset_x,
            "offsetY": offset_y,
            "zoom": zoom,
            "erasedRatio": round(erased_ratio, 4),
            "ridgeWidthRatio": round(ratio, 5),
            "minEngraveSizeMm": min_engrave_size_mm(ratio),
            "coverage": round(coverage, 4),
        },
    )


# ---------------------------------------------------------------- storage helpers
def _file(obj: storage.StoredObject) -> dict[str, Any]:
    return {
        "key": obj.key,
        "sha256": obj.sha256,
        "sizeBytes": obj.sizeBytes,
        "contentType": obj.contentType,
    }


def version_keys(prefix: str, version_id: str) -> tuple[str, str]:
    base = f"{prefix}versions/{version_id}/"
    return base + "fingerprint.svg", base + "fingerprint_alpha.png"


def _upload_version(prefix: str, rendered: RenderedVersion, strokes: list[Any]) -> dict[str, Any]:
    svg_key, png_key = version_keys(prefix, rendered.meta["versionId"])
    svg = storage.put_object(svg_key, rendered.svg, "image/svg+xml")
    png = storage.put_object(png_key, rendered.png, "image/png")
    return {
        **rendered.meta,
        "eraseStrokes": [s.model_dump() for s in strokes],
        "hull": rendered.hull,
        "vectorSvg": _file(svg),
        "alphaPng": {**_file(png), "widthPx": fp.CANVAS_SIZE, "heightPx": fp.CANVAS_SIZE},
        "createdAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def _put_manifest(key: str, doc: dict[str, Any]) -> storage.StoredObject:
    return storage.put_object(key, json.dumps(doc, indent=2).encode("utf-8"), "application/json")


def _load_manifest(key: str, *, strict: bool = False) -> dict[str, Any]:
    raw = storage.get_object(key) if strict else storage.get_object_optional(key)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


_VERSION_FIELDS = tuple(Version.model_fields)


def _version_model(entry: dict[str, Any]) -> Version:
    return Version(**{k: entry[k] for k in _VERSION_FIELDS})


def _outputs(entry: dict[str, Any], manifest: storage.StoredObject) -> FingerprintOutputs:
    return FingerprintOutputs(
        vectorSvg=OutputFile(**entry["vectorSvg"]),
        alphaPng=PngOutputFile(**entry["alphaPng"]),
        manifest=OutputFile(**_file(manifest)),
    )


def _entry_exists(entry: object) -> bool:
    try:
        return all(storage.object_exists(entry[k]["key"]) for k in ("vectorSvg", "alphaPng"))
    except (KeyError, TypeError):
        return False


def _decode_gray(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        raise image_unreadable()
    return img


def _manifest_current(doc: dict[str, Any], entry: dict[str, Any]) -> None:
    doc["currentVersionId"] = entry["versionId"]
    doc["outputs"] = {"vectorSvg": entry["vectorSvg"], "alphaPng": entry["alphaPng"]}
    doc["artwork"] = {"aspectRatio": 1.0, "hull": entry["hull"]}


# ---------------------------------------------------------------- endpoints
def process(req: FingerprintRequest) -> FingerprintResponse:
    settings = get_settings()
    logger.info(
        "fingerprint start", extra={"ctx": {"sourceId": req.sourceId, "rawKey": req.rawKey}}
    )
    raw = storage.get_object(req.rawKey)
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise image_unreadable()
    quality = fp.check_quality(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    base, cleanup = fp.build_base(image)
    ok, buf = cv2.imencode(".png", base)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    rendered = render_version(
        base,
        version_id=DEFAULT_VERSION_ID,
        shape=DEFAULT_SHAPE,
        offset_x=0.0,
        offset_y=0.0,
        zoom=1.0,
        strokes=[],
        potrace_binary=settings.potrace_binary,
        enforce=False,
    )
    base_obj = storage.put_object(req.outputPrefix + "base.png", buf.tobytes(), "image/png")
    base_doc = {**_file(base_obj), "widthPx": base.shape[1], "heightPx": base.shape[0]}
    entry = _upload_version(req.outputPrefix, rendered, [])
    doc: dict[str, Any] = {
        "kind": "FINGERPRINT",
        "processorVersion": PROCESSOR_VERSION,
        "request": {
            "sourceId": req.sourceId,
            "rawKey": req.rawKey,
            "outputPrefix": req.outputPrefix,
        },
        "quality": {**quality, "cleanup": cleanup},
        "base": base_doc,
        "versions": {DEFAULT_VERSION_ID: entry},
    }
    _manifest_current(doc, entry)
    if req.context is not None:
        doc["context"] = req.context.model_dump(exclude_none=True)
    manifest = _put_manifest(req.outputPrefix + "manifest.json", doc)
    return FingerprintResponse(
        processorVersion=PROCESSOR_VERSION,
        outputs=_outputs(entry, manifest),
        artwork=Artwork(**doc["artwork"]),
        quality=Quality(**quality),
        version=_version_model(entry),
        base=BaseImage(**base_doc),
    )


def render_crop(req: RenderCropRequest) -> RenderCropResponse:
    settings = get_settings()
    logger.info("fingerprint render-crop", extra={"ctx": {"sourceId": req.sourceId}})
    base_key = req.outputPrefix + "base.png"
    base_bytes = storage.get_object(base_key)  # 404 OBJECT_NOT_FOUND when missing
    base = _decode_gray(base_bytes)
    rendered = render_version(
        base,
        version_id=req.versionId,
        shape=req.shape,
        offset_x=req.offsetX,
        offset_y=req.offsetY,
        zoom=req.zoom,
        strokes=req.eraseStrokes,
        potrace_binary=settings.potrace_binary,
        enforce=True,
    )
    entry = _upload_version(req.outputPrefix, rendered, req.eraseStrokes)

    manifest_key = req.outputPrefix + "manifest.json"
    doc = _load_manifest(manifest_key)
    doc.setdefault("kind", "FINGERPRINT")
    doc["processorVersion"] = PROCESSOR_VERSION
    doc.setdefault("request", {"sourceId": req.sourceId, "outputPrefix": req.outputPrefix})
    base_doc = {
        "key": base_key,
        "sha256": storage_sha(base_bytes),
        "sizeBytes": len(base_bytes),
        "widthPx": base.shape[1],
        "heightPx": base.shape[0],
    }
    doc["base"] = base_doc
    versions = doc.get("versions") if isinstance(doc.get("versions"), dict) else {}
    versions[req.versionId] = entry  # same versionId: only this version is overwritten
    doc["versions"] = versions
    _manifest_current(doc, entry)
    if req.context is not None:
        doc["context"] = req.context.model_dump(exclude_none=True)
    manifest = _put_manifest(manifest_key, doc)
    return RenderCropResponse(
        processorVersion=PROCESSOR_VERSION,
        outputs=_outputs(entry, manifest),
        artwork=Artwork(**doc["artwork"]),
        version=_version_model(entry),
        base=BaseImage(**base_doc),
    )


def storage_sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def select_version(req: SelectVersionRequest) -> SelectVersionResponse:
    """Accept one version: delete every other version and `base.png`, rewrite the manifest."""
    logger.info("fingerprint select-version", extra={"ctx": {"sourceId": req.sourceId}})
    manifest_key = req.outputPrefix + "manifest.json"
    doc = _load_manifest(manifest_key, strict=True)
    versions = doc.get("versions") if isinstance(doc.get("versions"), dict) else {}
    entry = versions.get(req.versionId)
    if not isinstance(entry, dict) or not _entry_exists(entry):
        raise version_not_found(req.versionId)

    doomed: list[str | None] = [req.outputPrefix + "base.png", (doc.get("base") or {}).get("key")]
    for vid, other in versions.items():
        if vid == req.versionId:
            continue
        doomed += list(version_keys(req.outputPrefix, vid))
        if isinstance(other, dict):
            doomed += [(other.get(k) or {}).get("key") for k in ("vectorSvg", "alphaPng")]
    keep = {entry["vectorSvg"]["key"], entry["alphaPng"]["key"]}
    deleted: list[str] = []
    for key in dict.fromkeys(k for k in doomed if k and k not in keep):
        if storage.object_exists(key):
            storage.delete_object(key)
            deleted.append(key)

    doc.pop("base", None)
    doc["versions"] = {req.versionId: entry}
    doc["accepted"] = True
    _manifest_current(doc, entry)
    if req.context is not None:
        doc["context"] = req.context.model_dump(exclude_none=True)
    manifest = _put_manifest(manifest_key, doc)
    return SelectVersionResponse(
        processorVersion=PROCESSOR_VERSION,
        outputs=_outputs(entry, manifest),
        artwork=Artwork(**doc["artwork"]),
        version=_version_model(entry),
        deleted=deleted,
    )
