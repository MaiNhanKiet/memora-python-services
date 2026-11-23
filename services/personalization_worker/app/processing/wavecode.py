"""MEMORA_WAVECODE_v1 encoding and rendering (BAR_001_SW template, packages.SOUNDWAVE)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

from .wavecode_styles import DEFAULT_STYLE, SAMPLE_LEVELS, STYLES, WavecodeStyle

SCHEME = "MEMORA_WAVECODE_v1"
DATA_BARS = 18
BAR_COUNT = 23
MAX_LEVEL = 7
LEVEL_HEIGHTS_MM = (0.4, 0.6286, 0.8571, 1.0857, 1.3143, 1.5429, 1.7714, 2.0)
CODE_BAR_WIDTH_MM = 0.16  # CLASSIC bar width; defines the code extent and centring
BAR_PITCH_MM = 0.3696
HEIGHT_MM = 2.0
CODE_WIDTH_MM = (BAR_COUNT - 1) * BAR_PITCH_MM + CODE_BAR_WIDTH_MM  # 8.2912
CANVAS_WIDTH_MM = 8.5  # fixed engraving rectangle of the BAR_001_SW template
CANVAS_HEIGHT_MM = 2.0
X_OFFSET_MM = (CANVAS_WIDTH_MM - CODE_WIDTH_MM) / 2  # code centred horizontally
CENTER_Y_MM = CANVAS_HEIGHT_MM / 2
CENTER_LINE_HEIGHT_MM = 0.08
PX_PER_MM = 250
PNG_WIDTH = 2125  # 8.5 mm * 250 px/mm
PNG_HEIGHT = 500  # 2.0 mm * 250 px/mm
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


def _centre_line() -> tuple[float, float, float, float]:
    """(x, y, width, height) of the centre line, left edge of bar 0 to right edge of bar 22."""
    return (
        X_OFFSET_MM,
        CENTER_Y_MM - CENTER_LINE_HEIGHT_MM / 2,
        CODE_WIDTH_MM,
        CENTER_LINE_HEIGHT_MM,
    )


def get_style(style: str) -> WavecodeStyle:
    return STYLES[style]


def _bar_centre_x(i: int) -> float:
    return X_OFFSET_MM + CODE_BAR_WIDTH_MM / 2 + i * BAR_PITCH_MM


def _bar_rects(bars: list[int], width: float) -> list[tuple[float, float, float, float]]:
    """(x, y, width, height) per bar, centred on the same bar centre for every width."""
    out = []
    for i, level in enumerate(bars):
        h = LEVEL_HEIGHTS_MM[level]
        out.append((_bar_centre_x(i) - width / 2, CENTER_Y_MM - h / 2, width, h))
    return out


def line_points(bars: list[int], style: WavecodeStyle | None = None) -> list[tuple[float, float]]:
    """OSCILLOSCOPE polyline: 25 points, peaks alternate up (even bar) and down (odd bar)."""
    margin = (style or STYLES["OSCILLOSCOPE"]).line_margin_mm
    pts = [(_bar_centre_x(0) - margin, CENTER_Y_MM)]
    for i, level in enumerate(bars):
        sign = -1 if i % 2 == 0 else 1  # SVG y grows downwards: even bar goes up
        pts.append((_bar_centre_x(i), CENTER_Y_MM + sign * LEVEL_HEIGHTS_MM[level] / 2))
    pts.append((_bar_centre_x(len(bars) - 1) + margin, CENTER_Y_MM))
    return pts


def _num(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def render_svg(bars: list[int], levels: list[int] | None = None, style: str = DEFAULT_STYLE) -> str:
    """SVG in true mm on the 8.5 x 2.0 canvas.

    CLASSIC / HAIRLINE: filled rects only. CLASSIC also has a 0.08 mm centre line, thinner than the
    smallest bar (0.4 mm), so bar heights are unchanged for decoding. OSCILLOSCOPE: one
    unfilled <polyline> with a 0.09 mm stroke (laser software engraves it as a line).
    """
    st = get_style(style)
    if levels is None:
        levels = [*bars[1:10], *bars[12:21]]
    if st.kind == "LINE":
        points = " ".join(f"{_num(x)},{_num(y)}" for x, y in line_points(bars, st))
        body = (
            f'<polyline points="{points}" fill="none" stroke="#000" '
            f'stroke-width="{_num(st.line_width_mm)}" stroke-linejoin="round" '
            'stroke-linecap="round"/>'
        )
    else:
        shapes = []
        if st.centre_line:
            lx, ly, lw, lh = _centre_line()
            shapes.append(
                f'<rect x="{lx:.4f}" y="{ly:.4f}" width="{lw:.4f}" height="{lh:.4f}" '
                f'rx="{lh / 2:.4f}"/>'
            )
        shapes += [
            f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" rx="{w / 2:.4f}"/>'
            for x, y, w, h in _bar_rects(bars, st.bar_width_mm)
        ]
        body = f'<g fill="#000">{"".join(shapes)}</g>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_WIDTH_MM}mm" '
        f'height="{CANVAS_HEIGHT_MM}mm" viewBox="0 0 {CANVAS_WIDTH_MM} {CANVAS_HEIGHT_MM}" '
        f'data-style="{st.code}" data-levels="{",".join(str(v) for v in levels)}">'
        f"<title>{SCHEME}</title>{body}</svg>\n"
    )


def sample_svg(style: str) -> str:
    """SVG of the fixed demo levels, for the presets endpoint."""
    _, bars = build_bars(SAMPLE_LEVELS)
    return render_svg(bars, SAMPLE_LEVELS, style)


def render_png(bars: list[int], style: str = DEFAULT_STYLE) -> bytes:
    """2125x500 transparent canvas (250 px/mm), shapes opaque black, 4x supersampled."""
    st = get_style(style)
    ss = 4
    k = PX_PER_MM * ss
    mask = Image.new("L", (PNG_WIDTH * ss, PNG_HEIGHT * ss), 0)
    draw = ImageDraw.Draw(mask)
    if st.kind == "LINE":
        pts = [(x * k, y * k) for x, y in line_points(bars, st)]
        width = st.line_width_mm * k
        draw.line(pts, fill=255, width=round(width), joint="curve")
        r = width / 2
        for x, y in pts:  # round caps and joins
            draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
    else:
        if st.centre_line:
            lx, ly, lw, lh = _centre_line()
            draw.rounded_rectangle(
                (lx * k, ly * k, (lx + lw) * k, (ly + lh) * k), radius=lh / 2 * k, fill=255
            )
        for x, y, w, h in _bar_rects(bars, st.bar_width_mm):
            draw.rounded_rectangle(
                (x * k, y * k, (x + w) * k, (y + h) * k), radius=w / 2 * k, fill=255
            )
    alpha = mask.resize((PNG_WIDTH, PNG_HEIGHT), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (PNG_WIDTH, PNG_HEIGHT), (0, 0, 0, 0))
    image.putalpha(alpha)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
