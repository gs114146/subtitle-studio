"""Wrap a cue into centered lines inside the chosen subtitle band."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageFont

from .config import Settings, resolve_font
from .project import CueTiming


@dataclass
class PlacedWord:
    index: int
    text: str
    x: float
    y: float
    w: float
    h: float
    baseline: float

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "w": round(self.w, 1),
            "h": round(self.h, 1),
            "baseline": round(self.baseline, 1),
        }


@dataclass
class CueLayout:
    cue_index: int
    font_size: int
    words: list[PlacedWord]
    block_top: float
    block_bottom: float

    def to_dict(self) -> dict:
        return {
            "index": self.cue_index,
            "font_size": self.font_size,
            "block_top": round(self.block_top, 1),
            "block_bottom": round(self.block_bottom, 1),
            "words": [word.to_dict() for word in self.words],
        }


def _font(path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def _vertical_block(content_height: float, settings: Settings) -> tuple[float, float, float, float, bool]:
    """Return top, block_bottom, zone_top, zone_bottom, and whether the block fits the zone."""
    edge = float(settings.margin_bottom)
    zone = settings.height * settings.bottom_fraction
    position = (settings.position or "bottom").lower()

    if position == "top":
        zone_top = edge
        zone_bottom = zone
        top = zone_top
        block_bottom = top + content_height
        fits_zone = top >= zone_top - 1 and block_bottom <= zone_bottom + 1
        return top, block_bottom, zone_top, zone_bottom, fits_zone

    if position == "middle":
        zone_top = (settings.height - zone) / 2
        zone_bottom = zone_top + zone
        top = (settings.height - content_height) / 2
        block_bottom = top + content_height
        fits_zone = (
            content_height <= zone + 1
            and top >= 4
            and block_bottom <= settings.height - 4
        )
        return top, block_bottom, zone_top, zone_bottom, fits_zone

    zone_top = settings.height - zone
    zone_bottom = float(settings.height)
    bottom = settings.height - edge
    top = bottom - content_height
    if top < zone_top:
        top = zone_top
    block_bottom = top + content_height
    fits_zone = top >= zone_top - 1 and block_bottom <= settings.height - 4
    return top, block_bottom, zone_top, zone_bottom, fits_zone


def _place(cue: CueTiming, settings: Settings, font_path, size: int) -> CueLayout:
    font = _font(font_path, size)
    ascent, descent = font.getmetrics()
    line_height = (ascent + descent) * settings.line_spacing
    space = font.getlength(" ")
    max_width = settings.width * settings.max_width_ratio
    side = (settings.width - max_width) / 2

    lines: list[list[tuple[int, str, float]]] = []
    current: list[tuple[int, str, float]] = []
    current_width = 0.0
    for index, word in enumerate(cue.words):
        width = float(font.getlength(word.text))
        extra = width if not current else space + width
        if current and current_width + extra > max_width:
            lines.append(current)
            current = [(index, word.text, width)]
            current_width = width
        else:
            current.append((index, word.text, width))
            current_width += extra
    if current:
        lines.append(current)

    content_height = line_height * max(1, len(lines))
    top, block_bottom, zone_top, _zone_bottom, fits_zone = _vertical_block(content_height, settings)

    placed: list[PlacedWord] = []
    widest = 0.0
    for line_number, line in enumerate(lines):
        line_width = sum(item[2] for item in line) + space * max(0, len(line) - 1)
        widest = max(widest, line_width)
        cursor = (settings.width - line_width) / 2
        baseline = top + line_number * line_height + ascent
        word_top = baseline - ascent
        for index, text, width in line:
            placed.append(
                PlacedWord(
                    index=index,
                    text=text,
                    x=cursor,
                    y=word_top,
                    w=width,
                    h=float(ascent + descent),
                    baseline=baseline,
                )
            )
            cursor += width + space

    fits_width = widest <= max_width + 1
    layout = CueLayout(
        cue_index=cue.index,
        font_size=size,
        words=placed,
        block_top=top,
        block_bottom=block_bottom,
    )
    layout.fits = fits_width and fits_zone and all(side - 1 <= word.x for word in placed)  # type: ignore[attr-defined]
    layout.zone_top = zone_top  # type: ignore[attr-defined]
    return layout


def layout_cue(cue: CueTiming, settings: Settings, font_path=None) -> CueLayout:
    if not cue.words:
        return CueLayout(cue.index, settings.font_size, [], 0, 0)
    path = font_path or resolve_font(settings)
    size = settings.font_size
    chosen = _place(cue, settings, path, size)
    while size > 36 and not getattr(chosen, "fits", False):
        size -= 2
        chosen = _place(cue, settings, path, size)
    return chosen


def layout_project(project, settings: Settings) -> dict:
    path = resolve_font(settings)
    return {
        "width": settings.width,
        "height": settings.height,
        "orientation": settings.orientation,
        "position": settings.position,
        "font_family": "Subtitle",
        "cues": [layout_cue(cue, settings, path).to_dict() for cue in project.cues],
    }
