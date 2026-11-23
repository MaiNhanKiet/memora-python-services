"""MEMORA_WAVECODE_v1 encoding and rendering (BAR_001_SW template, packages.SOUNDWAVE)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

SCHEME = "MEMORA_WAVECODE_v1"
DATA_BARS = 18
BAR_COUNT = 23
MAX_LEVEL = 7
LEVEL_HEIGHTS_MM = (0.4, 0.6286, 0.8571, 1.0857, 1.3143, 1.5429, 1.7714, 2.0)
BAR_WIDTH_MM = 0.16
BAR_PITCH_MM = 0.3696
HEIGHT_MM = 2.0
WIDTH_MM = (BAR_COUNT - 1) * BAR_PITCH_MM + BAR_WIDTH_MM  # 8.2912
PNG_WIDTH = 2048
PNG_HEIGHT = 512
MAX_NUDGES = 8


def checksum_bars(levels: list[int]) -> list[int]:
    c = sum((i + 1) * level for i, level in enumerate(levels)) % 64
    return [c // 8, c % 8]


def build_bars(levels: list[int]) -> tuple[list[int], list[int]]:
    """Return (checkBars, bars[23])."""
    if len(levels) != DATA_BARS:
        raise ValueError("levels must contain 18 values")
    check = checksum_bars(levels)
    bars = [MAX_LEVEL, *levels[:9], check[0], MAX_LEVEL, *levels[9:], check[1], MAX_LEVEL]
    return check, bars


def quantize_envelope(rms: np.ndarray) -> tuple[list[int], np.ndarray]:
    """Normalize by max and quantize to 0..7. Returns (levels, scaled values in 0..7)."""
    peak = float(rms.max()) if rms.size else 0.0
    if peak <= 1e-9:
        return [0] * len(rms), np.zeros(len(rms))
    scaled = rms / peak * MAX_LEVEL
    levels = [int(np.clip(round(float(v)), 0, MAX_LEVEL)) for v in scaled]
    return levels, scaled


def nudge_candidates(
    levels: list[int], scaled: np.ndarray, limit: int = MAX_NUDGES
) -> list[list[int]]:
    """Sequences that differ by one data bar +/-1, closest quantization boundary first."""
    options: list[tuple[float, int, int]] = []
    for i, level in enumerate(levels):
        for step in (-1, 1):
            target = level + step
            if 0 <= target <= MAX_LEVEL:
                boundary = level + 0.5 * step
                options.append((abs(float(scaled[i]) - boundary), i, step))
    options.sort()
    result: list[list[int]] = []
    for _, i, step in options[:limit]:
        candidate = list(levels)
        candidate[i] += step
        result.append(candidate)
    return result


def _geometry(bars: list[int]) -> list[tuple[float, float, float]]:
    """(x, y, height) per bar in mm."""
    out = []
    for i, level in enumerate(bars):
        h = LEVEL_HEIGHTS_MM[level]
        out.append((i * BAR_PITCH_MM, HEIGHT_MM / 2 - h / 2, h))
    return out


def render_svg(bars: list[int]) -> str:
    rects = "".join(
        f'<rect x="{x:.4f}" y="{y:.4f}" width="{BAR_WIDTH_MM}" height="{h:.4f}" '
        f'rx="{BAR_WIDTH_MM / 2}"/>'
        for x, y, h in _geometry(bars)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH_MM:.4f} {HEIGHT_MM}">'
        f'<g fill="#000000">{rects}</g></svg>\n'
    )


def render_png(bars: list[int]) -> bytes:
    """2048x512 transparent canvas, bars opaque black, drawn directly (4x supersampled)."""
    ss = 4
    scale = PNG_WIDTH / WIDTH_MM
    y_offset = (PNG_HEIGHT - HEIGHT_MM * scale) / 2
    mask = Image.new("L", (PNG_WIDTH * ss, PNG_HEIGHT * ss), 0)
    draw = ImageDraw.Draw(mask)
    radius = BAR_WIDTH_MM / 2 * scale * ss
    for x, y, h in _geometry(bars):
        x0, y0 = x * scale * ss, (y * scale + y_offset) * ss
        x1, y1 = x0 + BAR_WIDTH_MM * scale * ss, y0 + h * scale * ss
        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=255)
    alpha = mask.resize((PNG_WIDTH, PNG_HEIGHT), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (PNG_WIDTH, PNG_HEIGHT), (0, 0, 0, 0))
    image.putalpha(alpha)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
