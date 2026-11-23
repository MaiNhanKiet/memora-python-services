"""Fingerprint image -> laser SVG (Potrace) + alpha PNG, ported from the earlier OpenCV pipeline."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..core.errors import fingerprint_region_not_found, tool_unavailable

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
BASE_SIZE = 2048
BLOB_MIN_AREA_RATIO = 0.0015
BLOB_FILL_RATIO = 0.85
BLOB_MIN_RADIUS_RATIO = 0.02
BORDER_STRAY_DISTANCE_RATIO = 0.03
BORDER_TINY_AREA_RATIO = 0.0005
MAX_CLEANUP_RATIO = 0.05


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


def _is_solid(mask: np.ndarray, area: int, shape: tuple[int, int]) -> bool:
    """True when the component is a thick, convex-looking blob rather than a thin ridge."""
    h, w = shape
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull_area = cv2.contourArea(cv2.convexHull(np.vstack(contours)))
    if hull_area <= 0 or area / hull_area <= BLOB_FILL_RATIO:
        return False
    min_radius = max(6.0, BLOB_MIN_RADIUS_RATIO * min(h, w))
    return float(cv2.distanceTransform(mask, cv2.DIST_L2, 3).max()) >= min_radius


def drop_solid_blobs(binary_img: np.ndarray) -> np.ndarray:
    """Drop solid blobs (no ridge texture): big, thick and almost equal to their convex hull.

    Conservative: a component goes only when it is larger than BLOB_MIN_AREA_RATIO of the image,
    its fill ratio against the convex hull is above BLOB_FILL_RATIO and it holds a disc of at
    least BLOB_MIN_RADIUS_RATIO of the image side (ridges are thin, so they never do).
    """
    inv = cv2.bitwise_not(binary_img)
    h, w = inv.shape[:2]
    min_area = BLOB_MIN_AREA_RATIO * h * w
    num, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    out = inv.copy()
    for i in range(1, num):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area >= min_area and _is_solid((labels == i).astype(np.uint8), area, (h, w)):
            out[labels == i] = 0
    return cv2.bitwise_not(out)


def drop_border_strays(binary_img: np.ndarray) -> np.ndarray:
    """Drop border-touching components that are clearly not ridges.

    A component touching the image border goes only when it is solid (thick disc and high fill
    ratio) or tiny (below BORDER_TINY_AREA_RATIO of the image) and far from the main ridge region.
    Elongated thin components that run off the border (real ridges) always stay.
    """
    inv = cv2.bitwise_not(binary_img)
    h, w = inv.shape[:2]
    num, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    if num <= 2:
        return binary_img
    areas = stats[:, cv2.CC_STAT_AREA]

    def touches(i: int) -> bool:
        x, y = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
        return bool(
            x == 0
            or y == 0
            or x + stats[i, cv2.CC_STAT_WIDTH] >= w
            or y + stats[i, cv2.CC_STAT_HEIGHT] >= h
        )

    big = BLOB_MIN_AREA_RATIO * h * w
    solid = {  # only components that could matter are tested (keeps this cheap on big images)
        i: _is_solid((labels == i).astype(np.uint8), int(areas[i]), (h, w))
        for i in range(1, num)
        if areas[i] >= big or touches(i)
    }
    candidates = [i for i in range(1, num) if not solid.get(i)] or list(range(1, num))
    main = max(candidates, key=lambda i: areas[i])  # a solid blob is never the "main region"
    near = cv2.distanceTransform((labels != main).astype(np.uint8), cv2.DIST_L2, 3)
    far = BORDER_STRAY_DISTANCE_RATIO * min(h, w)
    tiny = BORDER_TINY_AREA_RATIO * h * w
    out = inv.copy()
    for i in range(1, num):
        if i == main or not touches(i):
            continue
        mask = labels == i
        is_tiny_far = int(areas[i]) < tiny and float(near[mask].min()) > far
        if is_tiny_far or solid.get(i):
            out[mask] = 0
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


def clean_binary(image: np.ndarray) -> np.ndarray:
    """OpenCV pipeline -> cleaned, cropped binary (0 = ridge, 255 = background)."""
    return clean_binary_info(image)[0]


def _ink(img: np.ndarray) -> int:
    return int(cv2.countNonZero(cv2.bitwise_not(img)))


def clean_binary_info(image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Like clean_binary, plus cleanup info (solid/border removed ratios, borderFilterSkipped)."""
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
    ink0 = _ink(cleaned)
    cleaned = drop_solid_blobs(cleaned)
    ink1 = _ink(cleaned)
    bordered = drop_border_strays(cleaned)
    ink2 = _ink(bordered)
    skipped = ink0 > 0 and (ink0 - ink2) / ink0 > MAX_CLEANUP_RATIO
    if not skipped:  # safety net: never let the cleanup eat more than 5 % of the ink
        cleaned = bordered
    cleanup = {
        "solidRemovedRatio": round((ink0 - ink1) / ink0, 4) if ink0 else 0.0,
        "borderRemovedRatio": 0.0 if skipped or not ink0 else round((ink1 - ink2) / ink0, 4),
        "borderFilterSkipped": bool(skipped),
    }
    if cv2.countNonZero(cv2.bitwise_not(cleaned)) == 0:
        raise fingerprint_region_not_found()
    main_region = keep_fingerprint_main_region(cleaned, ERODE_SIZE)
    if cv2.countNonZero(cv2.bitwise_not(main_region)) == 0:
        raise fingerprint_region_not_found()
    return crop_to_fingerprint(main_region, PADDING), cleanup


def preprocess(image: np.ndarray) -> np.ndarray:
    """Cleaned print fitted on the 1024x1024 canvas (0 = ridge, 255 = background)."""
    return fit_to_square_canvas(clean_binary(image))


def build_base(image: np.ndarray, size: int = BASE_SIZE) -> tuple[np.ndarray, dict[str, Any]]:
    """Cleaned print on a square white canvas of `size` px, smooth edges, ridges black."""
    cropped, cleanup = clean_binary_info(image)
    h, w = cropped.shape[:2]
    side = max(h, w)
    square = np.full((side, side), 255, dtype=np.uint8)
    y0, x0 = (side - h) // 2, (side - w) // 2
    square[y0 : y0 + h, x0 : x0 + w] = cropped
    interp = cv2.INTER_CUBIC if size > side else cv2.INTER_AREA
    big = cv2.resize(square, (size, size), interpolation=interp)
    return np.where(big < 128, 0, 255).astype(np.uint8), cleanup


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
