"""Wavecode style presets: single source of truth (code, labels, decoder, geometry)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StyleCode = Literal["CLASSIC", "HAIRLINE", "OSCILLOSCOPE"]
DEFAULT_STYLE: StyleCode = "CLASSIC"
SAMPLE_LEVELS = [3, 5, 6, 4, 2, 1, 4, 6, 7, 5, 3, 2, 4, 5, 6, 3, 2, 1]


@dataclass(frozen=True)
class WavecodeStyle:
    code: StyleCode
    name: str
    description: str
    decoder: Literal["BAR_HEIGHT", "PEAK_HEIGHT"]
    kind: Literal["BARS", "LINE"]
    bar_width_mm: float = 0.16
    centre_line: bool = False
    line_width_mm: float = 0.09
    line_margin_mm: float = 0.15


STYLES: dict[str, WavecodeStyle] = {
    s.code: s
    for s in (
        WavecodeStyle(
            "CLASSIC",
            "Cổ điển",
            "Các thanh bo tròn rộng 0,16 mm nối với nhau bằng một đường tâm mảnh.",
            "BAR_HEIGHT",
            "BARS",
            bar_width_mm=0.16,
            centre_line=True,
        ),
        WavecodeStyle(
            "HAIRLINE",
            "Nét mảnh",
            "Các thanh mảnh rộng 0,10 mm, không có đường tâm, trông thanh thoát hơn.",
            "BAR_HEIGHT",
            "BARS",
            bar_width_mm=0.10,
        ),
        WavecodeStyle(
            "OSCILLOSCOPE",
            "Sóng liền",
            "Một nét liền mạch rộng 0,09 mm đi qua các đỉnh sóng như màn hình dao động ký.",
            "PEAK_HEIGHT",
            "LINE",
        ),
    )
}
