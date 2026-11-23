"""Fingerprint image -> laser SVG (Potrace) + alpha PNG, ported from the earlier OpenCV pipeline."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..core.config import get_settings
from ..core.errors import (
    fingerprint_region_not_found,
    image_unreadable,
    tool_unavailable,
)
from ..core.version import PROCESSOR_VERSION
from ..schemas import (
    Artwork,
    FingerprintOutputs,
    FingerprintRequest,
    FingerprintResponse,
    OutputFile,
    PngOutputFile,
    Quality,
)
from ..storage import s3 as storage

logger = logging.getLogger("personalization.fingerprint")

CANVAS_SIZE = 1024
CONTENT_RATIO = 0.85
MIN_AREA = 15
PADDING = 24
ERODE_SIZE = 4
ADAPTIVE_BLOCK = 31
ADAPTIVE_C = 4
BLUR_KSIZE = 5
CLAHE_CLIP = 2.5
MAX_SIDE = 1600
SVG_VIEWBOX = 1000
MAX_HULL_POINTS = 32
POTRACE_TIMEOUT_S = 40


def _odd_at_least(value: int, minimum: int = 3) -> int:
    v = max(minimum, int(value))
    return v + 1 if v % 2 == 0 else v


def ensure_black_ridges_white_bg(binary_img: np.ndarray) -> np.ndarray:
    img = binary_img.copy()
    if np.mean(img) < 127:
        img = cv2.bitwise_not(img)
    _, img = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)
    return img


def remove_small_components(binary_img: np.ndarray, min_area: int = MIN_AREA) -> np.ndarray:
    inv = cv2.bitwise_not(binary_img)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    keep = np.zeros(num, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    out = np.where(keep[labels], 255, 0).astype(np.uint8)
    return cv2.bitwise_not(out)


def keep_fingerprint_main_region(
    binary_img: np.ndarray, erode_size: int = ERODE_SIZE
) -> np.ndarray:
    """Keep the largest connected ridge region, mask out distant noise blobs."""
    inv = cv2.bitwise_not(binary_img)
    ridge_area = int(cv2.countNonZero(inv))
    if ridge_area == 0:
        return binary_img

    close_size = _odd_at_least(max(3, min(erode_size if erode_size > 0 else 5, 9)), 3)
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
    connected = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, kernel_close, iterations=1)

    seed = connected
    erode_k = 0
    if erode_size > 0:
        erode_k = _odd_at_least(min(int(erode_size), 11), 1)
        if erode_k >= 3:
            kernel_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_k, erode_k))
            eroded = cv2.erode(connected, kernel_erode, iterations=1)
            if int(cv2.countNonZero(eroded)) >= max(80, int(ridge_area * 0.03)):
                seed = eroded

    num, labels, stats, _ = cv2.connectedComponentsWithStats(seed, connectivity=8)
    if num <= 1:
        return binary_img

    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = np.zeros_like(inv)
    mask[labels == largest] = 255

    dilate_k = _odd_at_least(max(erode_k if erode_k > 0 else close_size, 5) + 2, 3)
    kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_k, dilate_k))
    mask = cv2.dilate(mask, kernel_dilate, iterations=2 if erode_size > 0 else 1)

    kept = cv2.bitwise_and(inv, mask)
    if int(cv2.countNonZero(kept)) < max(100, int(ridge_area * 0.25)):
        return binary_img
    return cv2.bitwise_not(kept)


def crop_to_fingerprint(binary_img: np.ndarray, padding: int = PADDING) -> np.ndarray:
    coords = cv2.findNonZero(cv2.bitwise_not(binary_img))
    if coords is None:
        return binary_img
    x, y, w, h = cv2.boundingRect(coords)
    h_img, w_img = binary_img.shape[:2]
    return binary_img[
        max(0, y - padding) : min(h_img, y + h + padding),
        max(0, x - padding) : min(w_img, x + w + padding),
    ]


def fit_to_square_canvas(
    binary_img: np.ndarray, canvas_size: int = CANVAS_SIZE, content_ratio: float = CONTENT_RATIO
) -> np.ndarray:
    canvas = np.full((canvas_size, canvas_size), 255, dtype=np.uint8)
    h, w = binary_img.shape[:2]
    if h == 0 or w == 0:
        return canvas
    target = max(1, int(canvas_size * content_ratio))
    scale = min(target / h, target / w)
    nh = max(1, round(h * scale))
    nw = max(1, round(w * scale))
    resized = cv2.resize(binary_img, (nw, nh), interpolation=cv2.INTER_NEAREST)
    y0 = (canvas_size - nh) // 2
    x0 = (canvas_size - nw) // 2
    canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
    return canvas


def check_quality(gray: np.ndarray) -> dict[str, Any]:
    """Contrast / variance based quality score (informational, never fails the job)."""
    std = float(np.std(gray))
    mean = float(np.mean(gray))
    score = min(100.0, round(std * 1.2, 1))
    if std < 12:
        message = "image too blurry or low contrast"
    elif mean < 20 or mean > 235:
        message = "image too dark or too bright"
    elif std < 25:
        message = "contrast acceptable but low"
    else:
        message = "contrast ok"
    return {"score": score, "message": message}


def preprocess(image: np.ndarray) -> np.ndarray:
    """OpenCV pipeline -> final 1024x1024 uint8 canvas (0 = ridge, 255 = background)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    h, w = gray.shape[:2]
    if max(h, w) > MAX_SIDE:
        scale = MAX_SIDE / max(h, w)
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    blur_k = _odd_at_least(BLUR_KSIZE, 3)
    blurred = cv2.GaussianBlur(gray, (blur_k, blur_k), 0)
    enhanced = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=(8, 8)).apply(blurred)
    binary_raw = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        _odd_at_least(ADAPTIVE_BLOCK, 3),
        ADAPTIVE_C,
    )
    binary = ensure_black_ridges_white_bg(binary_raw)
    cleaned = remove_small_components(binary, MIN_AREA)
    if cv2.countNonZero(cv2.bitwise_not(cleaned)) == 0:
        raise fingerprint_region_not_found()
    main_region = keep_fingerprint_main_region(cleaned, ERODE_SIZE)
    if cv2.countNonZero(cv2.bitwise_not(main_region)) == 0:
        raise fingerprint_region_not_found()
    cropped = crop_to_fingerprint(main_region, PADDING)
    return fit_to_square_canvas(cropped)


def alpha_png(final: np.ndarray) -> bytes:
    """RGBA PNG: black ridges opaque, background transparent."""
    rgba = np.zeros((*final.shape[:2], 4), dtype=np.uint8)
    rgba[final < 128, 3] = 255
    ok, buf = cv2.imencode(".png", rgba)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return buf.tobytes()


def compute_hull(final: np.ndarray) -> list[list[float]]:
    """Convex hull of ridge pixels, normalized 0..1 (origin top-left), at most 32 points."""
    ys, xs = np.nonzero(final < 128)
    if xs.size == 0:
        raise fingerprint_region_not_found()
    pts = np.stack([xs, ys], axis=1).astype(np.int32).reshape(-1, 1, 2)
    hull = cv2.convexHull(pts)
    eps = 0.5
    simplified = hull
    while len(simplified) > MAX_HULL_POINTS:
        simplified = cv2.approxPolyDP(hull, eps, True)
        eps *= 1.5
    size = float(final.shape[0])
    return [
        [
            round(min(1.0, (float(p[0][0]) + 0.5) / size), 4),
            round(min(1.0, (float(p[0][1]) + 0.5) / size), 4),
        ]
        for p in simplified
    ]


def _resolve_tool(binary: str) -> str:
    found = shutil.which(binary)
    if not found:
        raise tool_unavailable(Path(binary).name)
    return found


def vectorize(final: np.ndarray, potrace_binary: str, workdir: Path) -> str:
    """Run Potrace on the final canvas and return an SVG with viewBox 0 0 1000 1000."""
    exe = _resolve_tool(potrace_binary)
    bits = (final < 128).astype(np.uint8)
    h, w = bits.shape
    pbm = workdir / "fingerprint.pbm"
    pbm.write_bytes(f"P4\n{w} {h}\n".encode("ascii") + np.packbits(bits, axis=1).tobytes())
    raw_svg = workdir / "potrace.svg"
    cmd = [
        exe,
        str(pbm),
        "-s",
        "-o",
        str(raw_svg),
        "--turdsize",
        "3",
        "--alphamax",
        "0.75",
        "--opttolerance",
        "0.06",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=POTRACE_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise tool_unavailable("potrace") from exc
    if result.returncode != 0 or not raw_svg.is_file():
        raise tool_unavailable("potrace")
    return rescale_potrace_svg(raw_svg.read_text(encoding="utf-8", errors="ignore"))


def rescale_potrace_svg(text: str) -> str:
    """Rewrap Potrace paths into a unitless 1000x1000 viewBox."""
    paths = re.findall(r'<path[^>]*?\sd="([^"]*)"', text, flags=re.DOTALL)
    vb = re.search(r'viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+[\d.]+\s*"', text)
    sc = re.search(r"scale\(\s*([\d.]+)\s*,", text)
    vb_w = float(vb.group(1)) if vb else float(CANVAS_SIZE)
    path_scale = float(sc.group(1)) if sc else 0.1
    k = SVG_VIEWBOX / vb_w * path_scale
    body = "".join(f'<path d="{" ".join(d.split())}"/>' for d in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_VIEWBOX} {SVG_VIEWBOX}">'
        f'<g transform="translate(0,{SVG_VIEWBOX}) scale({k:.8f},{-k:.8f})" '
        f'fill="#000000" stroke="none">{body}</g></svg>\n'
    )


@dataclass
class FingerprintArtifacts:
    svg: bytes
    png: bytes
    hull: list[list[float]]
    quality: dict[str, Any]


def process_image_bytes(data: bytes, potrace_binary: str) -> FingerprintArtifacts:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise image_unreadable()
    quality = check_quality(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    final = preprocess(image)
    hull = compute_hull(final)
    with tempfile.TemporaryDirectory(prefix="fp-") as tmp:
        svg = vectorize(final, potrace_binary, Path(tmp))
    return FingerprintArtifacts(
        svg=svg.encode("utf-8"), png=alpha_png(final), hull=hull, quality=quality
    )


def run(req: FingerprintRequest) -> FingerprintResponse:
    settings = get_settings()
    ctx = {"sourceId": req.sourceId, "rawKey": req.rawKey}
    logger.info("fingerprint start", extra={"ctx": ctx})
    raw = storage.get_object(req.rawKey)
    art = process_image_bytes(raw, settings.potrace_binary)

    svg_out = storage.put_object(req.outputPrefix + "fingerprint.svg", art.svg, "image/svg+xml")
    png_out = storage.put_object(req.outputPrefix + "fingerprint_alpha.png", art.png, "image/png")
    artwork = {"aspectRatio": 1.0, "hull": art.hull}
    manifest_doc = {
        "kind": "FINGERPRINT",
        "processorVersion": PROCESSOR_VERSION,
        "request": {
            "sourceId": req.sourceId,
            "rawKey": req.rawKey,
            "outputPrefix": req.outputPrefix,
        },
        "outputs": {
            "vectorSvg": _file(svg_out),
            "alphaPng": {**_file(png_out), "widthPx": CANVAS_SIZE, "heightPx": CANVAS_SIZE},
        },
        "artwork": artwork,
        "quality": art.quality,
    }
    if req.context is not None:
        manifest_doc["context"] = req.context.model_dump(exclude_none=True)
    manifest_out = storage.put_object(
        req.outputPrefix + "manifest.json",
        json.dumps(manifest_doc, indent=2).encode("utf-8"),
        "application/json",
    )
    return FingerprintResponse(
        processorVersion=PROCESSOR_VERSION,
        outputs=FingerprintOutputs(
            vectorSvg=OutputFile(**_file(svg_out)),
            alphaPng=PngOutputFile(**_file(png_out), widthPx=CANVAS_SIZE, heightPx=CANVAS_SIZE),
            manifest=OutputFile(**_file(manifest_out)),
        ),
        artwork=Artwork(**artwork),
        quality=Quality(**art.quality),
    )


def _file(obj: storage.StoredObject) -> dict[str, Any]:
    return {
        "key": obj.key,
        "sha256": obj.sha256,
        "sizeBytes": obj.sizeBytes,
        "contentType": obj.contentType,
    }
