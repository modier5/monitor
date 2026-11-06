"""Vector-style UI icons drawn at runtime with Pillow.

CustomTkinter ships no icon font that looks the same on Windows and macOS, and
plain Unicode glyphs (``◫ ◎ ▣``) render at very different weights per platform.
Drawing the handful of glyphs we need with Pillow keeps the sidebar and lists
crisp on both systems without shipping extra binary assets.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

VIEW = 24.0
SUPERSAMPLE = 4


def _rgba(color: str) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255)


def _gradient_pixel(start: tuple[int, int, int], end: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(s + (e - s) * t) for s, e in zip(start, end))  # type: ignore[return-value]


class _Pen:
    """Tiny drawing helper working in the 24x24 design grid."""

    def __init__(self, size: int, stroke: float, color: str) -> None:
        self.scale = (size * SUPERSAMPLE) / VIEW
        side = int(round(VIEW * self.scale))
        self.image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)
        self.fill = _rgba(color)
        self.width = max(1, int(round(stroke * self.scale)))
        self.cap = self.width / 2

    def _pt(self, point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] * self.scale, point[1] * self.scale)

    def line(self, points: list[tuple[float, float]]) -> None:
        scaled = [self._pt(p) for p in points]
        self.draw.line(scaled, fill=self.fill, width=self.width, joint="curve")
        for x, y in (scaled[0], scaled[-1]):
            self.draw.ellipse((x - self.cap, y - self.cap, x + self.cap, y + self.cap), fill=self.fill)

    def loop(self, points: list[tuple[float, float]]) -> None:
        self.line([*points, points[0]])

    def circle(self, center: tuple[float, float], radius: float) -> None:
        x, y = self._pt(center)
        r = radius * self.scale
        self.draw.ellipse((x - r, y - r, x + r, y + r), outline=self.fill, width=self.width)

    def dot(self, center: tuple[float, float], radius: float) -> None:
        x, y = self._pt(center)
        r = radius * self.scale
        self.draw.ellipse((x - r, y - r, x + r, y + r), fill=self.fill)

    def arc(self, box: tuple[float, float, float, float], start: float, end: float) -> None:
        x0, y0 = self._pt((box[0], box[1]))
        x1, y1 = self._pt((box[2], box[3]))
        self.draw.arc((x0, y0, x1, y1), start=start, end=end, fill=self.fill, width=self.width)

    def polygon(self, points: list[tuple[float, float]]) -> None:
        self.draw.polygon([self._pt(p) for p in points], fill=self.fill)

    def rounded_rect(self, box: tuple[float, float, float, float], radius: float) -> None:
        x0, y0 = self._pt((box[0], box[1]))
        x1, y1 = self._pt((box[2], box[3]))
        self.draw.rounded_rectangle(
            (x0, y0, x1, y1), radius=radius * self.scale, outline=self.fill, width=self.width
        )


def _paint(name: str, pen: _Pen) -> None:
    if name == "activity":
        pen.line([(2.6, 12.0), (6.8, 12.0), (9.3, 5.4), (13.3, 18.6), (15.7, 12.0), (21.4, 12.0)])
    elif name == "gauge":
        pen.arc((4.2, 5.4, 19.8, 20.6), 180, 360)
        pen.line([(12.0, 16.0), (16.0, 10.0)])
        pen.dot((12.0, 16.0), 1.5)
    elif name == "target":
        pen.circle((12.0, 12.0), 7.4)
        pen.line([(12.0, 2.5), (12.0, 6.1)])
        pen.line([(12.0, 17.9), (12.0, 21.5)])
        pen.line([(2.5, 12.0), (6.1, 12.0)])
        pen.line([(17.9, 12.0), (21.5, 12.0)])
    elif name == "database":
        pen.arc((5.2, 3.4, 18.8, 8.4), 180, 360)
        pen.line([(5.2, 5.9), (5.2, 17.4)])
        pen.line([(18.8, 5.9), (18.8, 17.4)])
        pen.arc((5.2, 13.2, 18.8, 18.2), 0, 180)
        pen.arc((5.2, 8.7, 18.8, 13.7), 0, 180)
    elif name == "shield":
        pen.loop([(12.0, 3.2), (19.0, 6.2), (19.0, 11.4), (12.0, 20.8), (5.0, 11.4), (5.0, 6.2)])
        pen.line([(9.2, 11.9), (11.4, 14.2), (15.4, 9.5)])
    elif name == "bell":
        pen.arc((5.6, 3.8, 18.4, 16.4), 180, 360)
        pen.line([(5.6, 10.0), (5.6, 15.6)])
        pen.line([(18.4, 10.0), (18.4, 15.6)])
        pen.line([(4.0, 16.6), (20.0, 16.6)])
        pen.arc((9.8, 17.0, 14.2, 20.2), 0, 180)
    elif name == "folder":
        pen.loop([(3.2, 7.4), (9.2, 7.4), (11.0, 9.6), (20.8, 9.6), (20.8, 19.4), (3.2, 19.4)])
    elif name == "plus":
        pen.line([(12.0, 5.4), (12.0, 18.6)])
        pen.line([(5.4, 12.0), (18.6, 12.0)])
    elif name == "trash":
        pen.line([(3.8, 7.2), (20.2, 7.2)])
        pen.line([(9.4, 7.2), (9.4, 4.8), (14.6, 4.8), (14.6, 7.2)])
        pen.loop([(6.4, 7.2), (7.7, 20.2), (16.3, 20.2), (17.6, 7.2)])
        pen.line([(10.5, 10.8), (10.5, 16.6)])
        pen.line([(13.5, 10.8), (13.5, 16.6)])
    elif name == "app-window":
        pen.rounded_rect((3.4, 4.6, 20.6, 19.4), 2.4)
        pen.line([(3.4, 9.2), (20.6, 9.2)])
    elif name == "terminal":
        pen.rounded_rect((3.2, 4.6, 20.8, 19.4), 2.4)
        pen.line([(7.6, 10.0), (10.6, 12.4), (7.6, 14.8)])
        pen.line([(13.6, 14.8), (16.8, 14.8)])
    elif name == "hash":
        pen.line([(9.6, 3.6), (7.8, 20.4)])
        pen.line([(16.2, 3.6), (14.4, 20.4)])
        pen.line([(4.2, 9.2), (19.8, 9.2)])
        pen.line([(4.2, 14.8), (19.8, 14.8)])
    elif name == "play":
        pen.polygon([(8.6, 5.4), (19.0, 12.0), (8.6, 18.6)])
    elif name == "file":
        pen.loop([(6.2, 3.4), (13.6, 3.4), (18.2, 8.0), (18.2, 20.6), (6.2, 20.6)])
        pen.loop([(13.6, 3.4), (13.6, 8.0), (18.2, 8.0)])
        pen.line([(9.2, 12.8), (15.2, 12.8)])
        pen.line([(9.2, 16.4), (15.2, 16.4)])
    elif name == "check":
        pen.line([(5.4, 12.4), (10.0, 17.0), (18.6, 7.2)])
    elif name == "dot":
        pen.dot((12.0, 12.0), 4.4)
    else:  # pragma: no cover - defensive fallback
        pen.circle((12.0, 12.0), 7.0)


@lru_cache(maxsize=256)
def icon(name: str, color: str, size: int = 18, stroke: float = 1.9) -> Image.Image:
    """Return an RGBA icon rendered at ``size`` logical pixels."""
    pen = _Pen(size, stroke, color)
    _paint(name, pen)
    target = size * SUPERSAMPLE
    return pen.image.resize((target, target), Image.LANCZOS)


@lru_cache(maxsize=16)
def brand_mark(size: int = 34) -> Image.Image:
    """Gradient rounded tile with the activity glyph, used as the app mark."""
    side = size * SUPERSAMPLE
    tile = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    start = (43, 209, 189)
    end = (28, 124, 208)
    for y in range(side):
        draw.line([(0, y), (side, y)], fill=(*_gradient_pixel(start, end, y / max(1, side - 1)), 255))
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), radius=int(side * 0.3), fill=255)
    tile.putalpha(mask)
    glyph = icon("activity", "#FFFFFF", size=max(10, int(round(size * 0.62))), stroke=2.2)
    offset = ((side - glyph.width) // 2, (side - glyph.height) // 2)
    tile.alpha_composite(glyph, offset)
    return tile
