import io

import numpy as np
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


def test_svg_geometry():
    _, bars = wavecode.build_bars(LEVELS)
    svg = wavecode.render_svg(bars)
    assert 'viewBox="0 0 8.2912 2.0"' in svg
    assert svg.count("<rect") == 23
    assert 'width="0.16"' in svg and 'rx="0.08"' in svg
    # first bar: level 7 -> full height 2.0 starting at y=0
    assert '<rect x="0.0000" y="0.0000" width="0.16" height="2.0000"' in svg


def test_png_is_transparent_2048x512():
    _, bars = wavecode.build_bars(LEVELS)
    img = Image.open(io.BytesIO(wavecode.render_png(bars)))
    assert img.size == (2048, 512) and img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((20, 256))[3] > 200  # centre of the first bar
