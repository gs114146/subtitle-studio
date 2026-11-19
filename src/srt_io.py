"""Read SubRip subtitle files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SrtCue:
    index: int
    start: float
    end: float
    text: str


def parse_timestamp(value: str) -> float:
    text = value.strip().replace(".", ",")
    if "," in text:
        clock, fraction = text.split(",", 1)
        digits = re.sub(r"\D", "", fraction)[:3].ljust(3, "0")
        millis = int(digits)
    else:
        clock = text
        millis = 0
    parts = [int(part) for part in clock.strip().split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 1:
        hours = 0
        minutes = 0
        seconds = parts[0]
    else:
        raise ValueError(f"Could not read timestamp {value!r}")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_srt(text: str) -> list[SrtCue]:
    cleaned = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    cues: list[SrtCue] = []
    for block in re.split(r"\n\s*\n", cleaned):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        start_raw, end_raw = lines[time_index].split("-->", 1)
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw.strip().split()[0])
        body = " ".join(lines[time_index + 1 :]).strip()
        if not body:
            continue
        if end <= start:
            raise ValueError(f"Cue {len(cues) + 1} ends before it starts ({start:.3f}s to {end:.3f}s)")
        cues.append(SrtCue(index=len(cues) + 1, start=start, end=end, text=body))
    if not cues:
        raise ValueError("The SRT file has no subtitles")
    return cues


def load_srt(path: Path) -> list[SrtCue]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    return parse_srt(text)


def split_words(text: str) -> list[str]:
    return [part for part in text.split() if part]
