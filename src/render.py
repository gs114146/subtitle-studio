"""Draw highlighted subtitles and encode a transparent overlay."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import Settings, hex_to_rgba, resolve_font
from .layout import CueLayout, layout_cue
from .media import require_ffmpeg
from .project import Project, missing_timings
from .timing import active_word_index, pop_scale, visible_cue_index


def timeline_end(project: Project) -> float:
    cue_end = max((cue.end for cue in project.cues), default=0.0)
    word_end = max(
        (word.end or 0.0 for cue in project.cues for word in cue.words),
        default=0.0,
    )
    return max(project.audio_duration, cue_end, word_end)


def frame_count(project: Project, settings: Settings) -> int:
    return max(1, math.ceil(timeline_end(project) * settings.fps - 1e-6))


def _codec_args(codec: str) -> list[str]:
    if codec == "qtrle":
        return ["-c:v", "qtrle", "-pix_fmt", "argb"]
    if codec == "prores4444":
        # apl0 is Apple's vendor id, which Resolve recognises as ProRes 4444 with alpha.
        return [
            "-c:v", "prores_ks",
            "-profile:v", "4444",
            "-vendor", "apl0",
            "-alpha_bits", "16",
            "-pix_fmt", "yuva444p10le",
        ]
    raise ValueError(f"Unknown codec {codec}")


class Renderer:
    def __init__(self, project: Project, settings: Settings):
        self.project = project
        self.settings = settings
        self.font_path = resolve_font(settings)
        self.normal = hex_to_rgba(settings.color_normal)
        self.highlight = hex_to_rgba(settings.color_highlight)
        self.shadow = (0, 0, 0, int(255 * settings.shadow_opacity))
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._bases: dict[tuple, Image.Image] = {}
        self._layouts: dict[int, CueLayout] = {}
        self.blank = Image.new("RGBA", (settings.width, settings.height), (0, 0, 0, 0))

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        key = max(8, int(round(size)))
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(str(self.font_path), size=key)
        return self._fonts[key]

    def layout_for(self, cue) -> CueLayout:
        cached = self._layouts.get(cue.index)
        if cached is None:
            cached = layout_cue(cue, self.settings, self.font_path)
            self._layouts[cue.index] = cached
        return cached

    def _draw_word(self, draw: ImageDraw.ImageDraw, text: str, baseline: float, left: float, font, fill) -> None:
        position = (left, baseline)
        if self.settings.shadow_opacity > 0 and self.settings.shadow_offset > 0:
            offset = self.settings.shadow_offset
            draw.text((left + offset, baseline + offset), text, font=font, fill=self.shadow, anchor="ls")
        draw.text(position, text, font=font, fill=fill, anchor="ls")

    def base_image(self, cue) -> Image.Image:
        layout = self.layout_for(cue)
        key = (cue.index, tuple(word.text for word in cue.words), layout.font_size)
        cached = self._bases.get(key)
        if cached is not None:
            return cached
        image = Image.new("RGBA", (self.settings.width, self.settings.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = self.font(layout.font_size)
        for placed in layout.words:
            self._draw_word(draw, placed.text, placed.baseline, placed.x, font, self.normal)
        self._bases[key] = image
        return image

    def frame(self, time: float) -> Image.Image:
        cue_index = visible_cue_index(self.project.cues, time)
        cue = next((item for item in self.project.cues if item.index == cue_index), None)
        if cue is None or not cue.words:
            return self.blank.copy()
        image = self.base_image(cue).copy()
        active = active_word_index(cue.words, time, self.settings.pause_hold_ms / 1000.0)
        if active is None:
            return image
        layout = self.layout_for(cue)
        placed = next((item for item in layout.words if item.index == active), None)
        word = cue.words[active]
        if placed is None or word.start is None:
            return image
        scale = pop_scale(time, word.start, self.settings.animation_ms / 1000.0, self.settings.pop_scale)
        draw = ImageDraw.Draw(image)
        size = max(8, int(round(layout.font_size * scale)))
        font = self.font(size)
        if scale == 1 or size == layout.font_size:
            left = placed.x
            baseline = placed.baseline
        else:
            ascent, descent = font.getmetrics()
            width = float(font.getlength(placed.text))
            height = float(ascent + descent)
            center_x = placed.x + placed.w / 2
            center_y = placed.y + placed.h / 2
            left = center_x - width / 2
            baseline = center_y - height / 2 + ascent
        self._draw_word(draw, placed.text, baseline, left, font, self.highlight)
        return image


def render_png(project: Project, settings: Settings, time: float) -> bytes:
    import io

    image = Renderer(project, settings).frame(time)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def export_overlay(project: Project, settings: Settings, output: Path, progress=None) -> Path:
    require_ffmpeg()
    if missing_timings(project):
        raise ValueError("Generate word timings before exporting. Some words still have no start or end.")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = frame_count(project, settings)
    renderer = Renderer(project, settings)
    log_path = output.with_suffix(output.suffix + ".ffmpeg.log")
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{settings.width}x{settings.height}",
        "-r",
        str(settings.fps),
        "-i",
        "pipe:0",
        *_codec_args(settings.codec),
        str(output),
    ]
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log_file)
        assert process.stdin is not None
        try:
            for index in range(total):
                image = renderer.frame(index / settings.fps)
                process.stdin.write(image.tobytes())
                if progress and (index % 15 == 0 or index + 1 == total):
                    progress(f"Rendering overlay… frame {index + 1} of {total}")
        except BrokenPipeError:
            process.wait()
        finally:
            if process.stdin:
                process.stdin.close()
        return_code = process.wait()
    if return_code != 0:
        detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg could not encode the overlay. {detail}".strip())
    log_path.unlink(missing_ok=True)
    if progress:
        progress(f"Exported {output}")
    return output
