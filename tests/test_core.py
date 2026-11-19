"""Checks that do not need the speech model."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.align import _build_cue_words  # noqa: E402
from src.config import Settings  # noqa: E402
from src.layout import layout_cue  # noqa: E402
from src.project import CueTiming, WordTiming, replace_cue_sentence  # noqa: E402
from src.render import Renderer, export_overlay  # noqa: E402
from src.srt_io import parse_srt  # noqa: E402
from src.timing import active_word_index, pop_scale  # noqa: E402


def words(*pairs: tuple[str, float, float]) -> list[WordTiming]:
    return [WordTiming(text, start, end) for text, start, end in pairs]


class TimingTests(unittest.TestCase):
    def test_highlight_follows_speech_and_pauses(self):
        spoken = words(("Hello", 0.20, 0.50), ("there", 0.60, 0.90))
        self.assertIsNone(active_word_index(spoken, 0.05, 0.22))
        self.assertEqual(active_word_index(spoken, 0.30, 0.22), 0)
        self.assertEqual(active_word_index(spoken, 0.55, 0.22), 0)
        self.assertIsNone(active_word_index(spoken, 0.55, 0.05))
        self.assertEqual(active_word_index(spoken, 0.60, 0.05), 1)

    def test_pop_peaks_once_then_settles(self):
        self.assertEqual(pop_scale(1.0, 1.0, 0.14, 1.08), 1.0)
        self.assertAlmostEqual(pop_scale(1.07, 1.0, 0.14, 1.08), 1.08, places=3)
        self.assertEqual(pop_scale(1.20, 1.0, 0.14, 1.08), 1.0)


class SrtTests(unittest.TestCase):
    def test_parse_multiline_cue(self):
        text = """1
00:00:01.000 --> 00:00:02,500
Hello there
friends

2
00:00:03,000 --> 00:00:04,000
Second line
"""
        cues = parse_srt(text)
        self.assertEqual(cues[0].text, "Hello there friends")
        self.assertAlmostEqual(cues[0].start, 1.0)
        self.assertAlmostEqual(cues[0].end, 2.5)
        self.assertEqual(cues[1].text, "Second line")

    def test_sentence_edit_keeps_known_times(self):
        cue = CueTiming(
            1,
            0,
            2,
            "Hello there",
            words(("Hello", 0.1, 0.4), ("there", 0.5, 0.8)),
        )
        replace_cue_sentence(cue, "Hello kind there")
        self.assertEqual([word.text for word in cue.words], ["Hello", "kind", "there"])
        self.assertEqual(cue.words[0].start, 0.1)
        self.assertIsNone(cue.words[1].start)
        self.assertTrue(cue.words[1].flagged)
        self.assertEqual(cue.words[2].start, 0.5)


class AlignMappingTests(unittest.TestCase):
    def test_srt_words_receive_heard_times(self):
        cue = CueTiming(
            1,
            0.0,
            3.0,
            "Welcome to the ocean",
            [WordTiming(text) for text in ["Welcome", "to", "the", "ocean"]],
        )
        aligned = [
            {"text": "Welcome", "start": 0.30, "end": 0.80, "probability": 0.9},
            {"text": "to", "start": 1.10, "end": 1.25, "probability": 0.8},
            {"text": "the", "start": 1.70, "end": 1.90, "probability": 0.8},
            {"text": "ocean", "start": 2.20, "end": 2.70, "probability": 0.4},
        ]
        built = _build_cue_words(cue, aligned, Settings())
        self.assertEqual([round(word.start, 2) for word in built], [0.30, 1.10, 1.70, 2.20])
        self.assertGreater(built[2].start - built[1].end, 0.3)
        self.assertFalse(any(word.note.startswith("Timing was estimated") for word in built))

    def test_unheard_word_is_flagged(self):
        cue = CueTiming(
            1,
            0.0,
            2.0,
            "Hello missing friend",
            [WordTiming(text) for text in ["Hello", "missing", "friend"]],
        )
        aligned = [
            {"text": "Hello", "start": 0.20, "end": 0.50, "probability": 0.9},
            {"text": "friend", "start": 1.20, "end": 1.60, "probability": 0.9},
        ]
        built = _build_cue_words(cue, aligned, Settings())
        self.assertTrue(built[1].flagged)
        self.assertLessEqual(built[0].end, built[1].start)
        self.assertLessEqual(built[1].end, built[2].start)
        self.assertGreater(built[1].end, built[1].start)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(fps=10, animation_ms=0, pause_hold_ms=80)
        self.project_cues = [
            CueTiming(
                1,
                0.0,
                1.0,
                "Hello there",
                words(("Hello", 0.15, 0.40), ("there", 0.62, 0.90)),
            )
        ]

    def _project(self):
        from src.project import Project

        return Project("audio.wav", "captions.srt", 1.0, self.project_cues)

    def test_highlight_is_yellow_and_background_is_clear(self):
        renderer = Renderer(self._project(), self.settings)
        blank = renderer.frame(1.2)
        self.assertIsNone(blank.getbbox())
        shown = renderer.frame(0.25)
        zone_top = int(self.settings.height * (1 - self.settings.bottom_fraction))
        box = shown.getbbox()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box[1], zone_top - 2)
        pixels = list(shown.get_flattened_data())
        self.assertTrue(any(pixel[3] == 0 for pixel in pixels[::50]))
        self.assertEqual(shown.getpixel((8, 8))[3], 0)
        self.assertTrue(any(pixel[0] > 230 and pixel[1] > 170 and pixel[2] < 120 and pixel[3] > 200 for pixel in pixels))
        self.assertTrue(any(pixel[0] > 230 and pixel[1] > 230 and pixel[2] > 230 and pixel[3] > 200 for pixel in pixels))
        paused = renderer.frame(0.50)
        paused_pixels = list(paused.get_flattened_data())
        self.assertFalse(any(pixel[0] > 230 and pixel[1] > 170 and pixel[2] < 120 and pixel[3] > 200 for pixel in paused_pixels))

    def test_long_line_wraps_inside_the_bottom_band(self):
        cue = CueTiming(
            1,
            0,
            2,
            "Water covers most of the planet and the deep ocean is still being explored",
            [WordTiming(word) for word in "Water covers most of the planet and the deep ocean is still being explored".split()],
        )
        placed = layout_cue(cue, self.settings)
        zone_top = self.settings.height * (1 - self.settings.bottom_fraction)
        self.assertGreaterEqual(placed.block_top, zone_top - 1)
        self.assertLessEqual(placed.block_bottom, self.settings.height)
        self.assertGreater(max(word.y for word in placed.words), min(word.y for word in placed.words))

    def test_top_and_middle_positions(self):
        cue = CueTiming(
            1,
            0,
            2,
            "Hello there friends",
            words(("Hello", 0.1, 0.3), ("there", 0.4, 0.6), ("friends", 0.7, 0.9)),
        )
        top = layout_cue(cue, Settings(position="top", margin_bottom=120, animation_ms=0))
        self.assertLessEqual(top.block_top, 130)
        self.assertLess(top.block_bottom, self.settings.height * 0.45)

        middle = layout_cue(cue, Settings(position="middle", animation_ms=0))
        center = self.settings.height / 2
        self.assertLess(middle.block_top, center)
        self.assertGreater(middle.block_bottom, center)

    def test_landscape_frame_keeps_text_inside(self):
        cue = CueTiming(
            1,
            0,
            2,
            "Landscape overlays still sit inside the chosen band",
            [WordTiming(word) for word in "Landscape overlays still sit inside the chosen band".split()],
        )
        settings = Settings(
            orientation="landscape",
            width=1920,
            height=1080,
            position="bottom",
            margin_bottom=120,
            font_size=64,
            animation_ms=0,
        )
        placed = layout_cue(cue, settings)
        self.assertGreaterEqual(placed.block_top, settings.height * (1 - settings.bottom_fraction) - 1)
        self.assertLessEqual(placed.block_bottom, settings.height)
        self.assertTrue(all(0 <= word.x <= settings.width for word in placed.words))

    def test_prores_overlay_keeps_alpha(self):
        from src.project import Project

        project = Project("audio.wav", "captions.srt", 0.4, [
            CueTiming(1, 0.0, 0.4, "Hello there", words(("Hello", 0.05, 0.20), ("there", 0.24, 0.38)))
        ])
        settings = Settings(fps=10, animation_ms=0, pause_hold_ms=40)
        out_dir = ROOT / "work" / "test"
        out_dir.mkdir(parents=True, exist_ok=True)
        movie = out_dir / "alpha.mov"
        still = out_dir / "composite.png"
        export_overlay(project, settings, movie)
        probe = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,pix_fmt,width,height",
                "-of", "default=noprint_wrappers=1",
                str(movie),
            ],
            text=True,
        )
        self.assertIn("codec_name=prores", probe)
        self.assertIn("width=1080", probe)
        self.assertIn("height=1920", probe)
        pix = next(line.split("=", 1)[1] for line in probe.splitlines() if line.startswith("pix_fmt="))
        self.assertTrue(pix.startswith("yuva444p"), pix)
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=red:s=1080x1920:r=10:d=1",
                "-i", str(movie),
                "-filter_complex", "overlay=format=auto",
                "-frames:v", "1",
                str(still),
            ]
        )
        image = Image.open(still).convert("RGB")
        corner = image.getpixel((10, 10))
        self.assertGreater(corner[0], 200)
        self.assertLess(corner[1], 40)
        self.assertLess(corner[2], 40)
        white = yellow = 0
        for y in range(int(settings.height * 0.7), settings.height):
            for x in range(0, settings.width, 2):
                pixel = image.getpixel((x, y))
                if pixel[0] > 220 and pixel[1] > 220 and pixel[2] > 220:
                    white += 1
        self.assertGreater(white, 100)
        highlighted = still.with_name("highlighted.png")
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(movie), "-vf", "select=eq(n\\,1)", "-frames:v", "1", str(highlighted),
            ]
        )
        frame = Image.open(highlighted)
        for pixel in frame.get_flattened_data():
            if len(pixel) > 3 and pixel[3] > 200 and pixel[0] > 220 and pixel[1] > 160 and pixel[2] < 140:
                yellow += 1
                break
        self.assertGreater(yellow, 0)


if __name__ == "__main__":
    unittest.main()
