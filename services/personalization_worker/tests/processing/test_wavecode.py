import io
import re

import numpy as np
import pytest
from PIL import Image

from app.processing import wavecode

LEVELS = [3, 5, 6, 4, 2, 1, 4, 6, 7, 5, 3, 2, 4, 5, 6, 3, 2, 1]
BARS = [7, 3, 5, 6, 4, 2, 1, 4, 6, 7, 5, 7, 5, 3, 2, 4, 5, 6, 3, 2, 1, 5, 7]


def test_contract_example_checksum_and_layout():
    assert sum((i + 1) * v for i, v in enumerate(LEVELS)) == 621
    check, bars = wavecode.build_bars(LEVELS)
    assert check == [5, 5]
    assert bars == BARS
    assert len(bars) == 23
    assert bars[0] == bars[11] == bars[22] == 7


def test_quantize_normalizes_by_max_and_rounds():
    levels, scaled = wavecode.quantize_envelope(np.array([0.0, 0.25, 1.0]))
    assert levels == [0, 2, 7]  # 0.25 * 7 = 1.75 -> 2
    assert scaled[2] == 7.0


def test_all_silent_gives_level_zero():
    levels, scaled = wavecode.quantize_envelope(np.zeros(18))
    assert levels == [0] * 18
    assert not scaled.any()


def test_nudge_candidates_closest_boundary_first():
    levels = [3] * 18
    scaled = np.full(18, 3.0)
    scaled[4] = 3.45  # nearly at the 3.5 boundary -> +1 first
    scaled[9] = 2.55  # nearly at the 2.5 boundary -> -1 second
    cands = wavecode.nudge_candidates(levels, scaled)
    assert len(cands) == 8
    assert cands[0][4] == 4
    assert cands[1][9] == 2
    for c in cands:
        assert sum(a != b for a, b in zip(c, levels, strict=True)) == 1
        assert all(0 <= v <= 7 for v in c)


def test_nudge_stays_in_range():
    levels = [0] * 9 + [7] * 9
    cands = wavecode.nudge_candidates(levels, np.array([0.0] * 9 + [7.0] * 9))
    assert cands
    for c in cands:
        assert all(0 <= v <= 7 for v in c)


def _rects(svg):
    return [
        tuple(float(v) for v in m)
        for m in re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
    ]


def test_svg_canvas_and_laser_friendly():
    _, bars = wavecode.build_bars(LEVELS)
    svg = wavecode.render_svg(bars)
    assert 'viewBox="0 0 8.5 2.0"' in svg
    assert 'width="8.5mm"' in svg and 'height="2.0mm"' in svg
    assert "<title>MEMORA_WAVECODE_v1</title>" in svg
    assert 'data-levels="' + ",".join(map(str, LEVELS)) + '"' in svg
    assert svg.count('fill="') == 1 and 'fill="#000"' in svg
    for banned in ("stroke", "transform", "opacity", "gradient", "filter"):
        assert banned not in svg


def test_svg_bar_geometry_matches_spec():
    _, bars = wavecode.build_bars(LEVELS)
    svg = wavecode.render_svg(bars)
    assert svg.count("<rect") == 24
    rects = _rects(svg)
    line, bar_rects = rects[0], rects[1:]
    assert len(bar_rects) == 23
    offset = (8.5 - 8.2912) / 2
    for i, (x, y, w, h) in enumerate(bar_rects):
        height = wavecode.LEVEL_HEIGHTS_MM[bars[i]]
        assert x == pytest.approx(offset + i * 0.3696, abs=1e-4)
        assert w == 0.16
        assert h == pytest.approx(height, abs=1e-4)
        assert y + h / 2 == pytest.approx(1.0, abs=1e-4)  # mirrored about the centre line
    assert 'rx="0.0800"' in svg
    # first bar: level 7 -> full height starting at y=0; last bar ends at offset + 8.2912
    assert bar_rects[0][1] == 0.0 and bar_rects[0][3] == 2.0
    assert bar_rects[22][0] + bar_rects[22][2] == pytest.approx(offset + 8.2912, abs=1e-4)
    # centre line: 0.08 mm, spans left edge of bar 0 to right edge of bar 22
    assert line[0] == pytest.approx(offset, abs=1e-4)
    assert line[2] == pytest.approx(8.2912, abs=1e-4)
    assert line[3] == 0.08 and line[1] == pytest.approx(0.96, abs=1e-4)
    assert 'rx="0.0400"' in svg


def test_png_is_transparent_2125x500():
    _, bars = wavecode.build_bars(LEVELS)
    img = Image.open(io.BytesIO(wavecode.render_png(bars)))
    assert img.size == (2125, 500) and img.mode == "RGBA"
    assert wavecode.PNG_WIDTH / wavecode.PNG_HEIGHT == pytest.approx(8.5 / 2.0)
    for corner in ((0, 0), (2124, 0), (0, 499), (2124, 499)):
        assert img.getpixel(corner)[3] == 0
    offset = (8.5 - 8.2912) / 2
    for i, level in enumerate(bars):
        cx = round((offset + i * 0.3696 + 0.08) * 250)
        px = img.getpixel((cx, 250))
        assert px[3] == 255 and px[:3] == (0, 0, 0)
        half = wavecode.LEVEL_HEIGHTS_MM[level] / 2 * 250
        top = round(250 - half) - 4
        if top >= 0:
            assert img.getpixel((cx, top))[3] == 0  # just above the bar top


STYLE_ROOT = ('viewBox="0 0 8.5 2.0"', 'width="8.5mm"', 'height="2.0mm"')


@pytest.mark.parametrize("style", ["CLASSIC", "HAIRLINE", "OSCILLOSCOPE"])
def test_every_style_svg_root_and_png(style):
    _, bars = wavecode.build_bars(LEVELS)
    svg = wavecode.render_svg(bars, style=style)
    for part in STYLE_ROOT:
        assert part in svg
    assert f'data-style="{style}"' in svg
    assert "<title>MEMORA_WAVECODE_v1</title>" in svg
    assert 'data-levels="' + ",".join(map(str, LEVELS)) + '"' in svg
    img = Image.open(io.BytesIO(wavecode.render_png(bars, style)))
    assert img.size == (2125, 500) and img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0


def test_default_style_is_classic():
    _, bars = wavecode.build_bars(LEVELS)
    assert wavecode.render_svg(bars) == wavecode.render_svg(bars, style="CLASSIC")
    assert 'data-style="CLASSIC"' in wavecode.render_svg(bars)


def test_hairline_has_thin_bars_and_no_centre_line():
    _, bars = wavecode.build_bars(LEVELS)
    svg = wavecode.render_svg(bars, style="HAIRLINE")
    rects = _rects(svg)
    assert svg.count("<rect") == 23 and len(rects) == 23
    offset = (8.5 - 8.2912) / 2
    for i, (x, _y, w, h) in enumerate(rects):
        assert w == 0.1
        assert x + w / 2 == pytest.approx(offset + 0.08 + i * 0.3696, abs=1e-4)
        assert h == pytest.approx(wavecode.LEVEL_HEIGHTS_MM[bars[i]], abs=1e-4)
    assert 'rx="0.0500"' in svg and "polyline" not in svg


def test_classic_has_23_rects_and_centre_line():
    _, bars = wavecode.build_bars(LEVELS)
    assert wavecode.render_svg(bars, style="CLASSIC").count("<rect") == 24


def test_oscilloscope_is_one_alternating_polyline():
    _, bars = wavecode.build_bars(LEVELS)
    svg = wavecode.render_svg(bars, style="OSCILLOSCOPE")
    assert svg.count("<polyline") == 1 and "<rect" not in svg
    for attr in (
        'fill="none"',
        'stroke-width="0.09"',
        'stroke-linejoin="round"',
        'stroke-linecap="round"',
    ):
        assert attr in svg
    raw = re.search(r'points="([^"]+)"', svg).group(1)
    pts = [tuple(float(v) for v in p.split(",")) for p in raw.split()]
    assert len(pts) == 25
    centre0 = (8.5 - 8.2912) / 2 + 0.08
    assert pts[0] == pytest.approx((centre0 - 0.15, 1.0), abs=1e-4)
    assert pts[-1][1] == 1.0
    assert pts[-1][0] == pytest.approx(centre0 + 22 * 0.3696 + 0.15, abs=1e-4)
    for i, level in enumerate(bars):
        x, y = pts[i + 1]
        half = wavecode.LEVEL_HEIGHTS_MM[level] / 2
        assert x == pytest.approx(centre0 + i * 0.3696, abs=1e-4)
        assert y == pytest.approx(1.0 - half if i % 2 == 0 else 1.0 + half, abs=1e-4)


def test_sample_svg_matches_reference_points():
    svg = wavecode.sample_svg("OSCILLOSCOPE")
    assert 'points="0.0344,1 0.1844,0 0.554,1.5429 0.9236,0.2286' in svg
    assert svg.endswith(
        '8.3156,0 8.4656,1" fill="none" stroke="#000" stroke-width="0.09" '
        'stroke-linejoin="round" stroke-linecap="round"/></svg>\n'
    )
