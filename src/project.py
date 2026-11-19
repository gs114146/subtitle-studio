"""Editable timing project saved as JSON."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .srt_io import load_srt, split_words


@dataclass
class WordTiming:
    text: str
    start: float | None = None
    end: float | None = None
    probability: float | None = None
    flagged: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("start", "end"):
            if data[key] is not None:
                data[key] = round(float(data[key]), 3)
        if data["probability"] is not None:
            data["probability"] = round(float(data["probability"]), 3)
        return data


@dataclass
class CueTiming:
    index: int
    start: float
    end: float
    text: str
    words: list[WordTiming] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "text": self.text,
            "words": [word.to_dict() for word in self.words],
        }


@dataclass
class Project:
    audio_path: str
    srt_path: str
    audio_duration: float
    cues: list[CueTiming]
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "audio_path": self.audio_path,
            "srt_path": self.srt_path,
            "audio_duration": round(float(self.audio_duration), 3),
            "cues": [cue.to_dict() for cue in self.cues],
        }


def normalize_token(text: str) -> str:
    return re.sub(r"[^0-9a-z']+", "", text.lower())


def refresh_cue_text(project: Project) -> None:
    for cue in project.cues:
        spoken = [word.text.strip() for word in cue.words if word.text.strip()]
        if spoken:
            cue.text = " ".join(spoken)
            cue.words = [word for word in cue.words if word.text.strip()]


def project_from_srt(audio_path: Path, srt_path: Path, audio_duration: float) -> Project:
    cues = []
    for cue in load_srt(srt_path):
        words = [
            WordTiming(text=token, flagged=True, note="Not aligned yet")
            for token in split_words(cue.text)
        ]
        cues.append(CueTiming(index=cue.index, start=cue.start, end=cue.end, text=cue.text, words=words))
    if not cues:
        raise ValueError("The SRT file has no subtitles")
    return Project(
        audio_path=str(audio_path),
        srt_path=str(srt_path),
        audio_duration=audio_duration,
        cues=cues,
    )


def replace_cue_sentence(cue: CueTiming, sentence: str) -> None:
    """Keep timing for words that still match, and mark new words for alignment."""
    incoming = split_words(sentence)
    if not incoming:
        raise ValueError(f"Cue {cue.index} has no words")
    unused = list(enumerate(cue.words))
    rebuilt: list[WordTiming] = []
    for token in incoming:
        match_at = None
        for position, (old_index, old) in enumerate(unused):
            if normalize_token(old.text) == normalize_token(token) and normalize_token(token):
                match_at = position
                break
        if match_at is None:
            rebuilt.append(WordTiming(text=token, flagged=True, note="Not aligned yet"))
            continue
        _, old = unused.pop(match_at)
        old.text = token
        rebuilt.append(old)
    cue.words = rebuilt
    cue.text = " ".join(word.text for word in rebuilt)


def _word_from_dict(raw: dict) -> WordTiming:
    return WordTiming(
        text=str(raw.get("text", "")).strip(),
        start=_optional_float(raw.get("start")),
        end=_optional_float(raw.get("end")),
        probability=_optional_float(raw.get("probability")),
        flagged=bool(raw.get("flagged", False)),
        note=str(raw.get("note", "")),
    )


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def project_from_dict(raw: dict) -> Project:
    cues = []
    for item in raw.get("cues", []):
        words = [_word_from_dict(word) for word in item.get("words", [])]
        words = [word for word in words if word.text]
        cue = CueTiming(
            index=int(item["index"]),
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item.get("text", "")),
            words=words,
        )
        if cue.end <= cue.start:
            raise ValueError(f"Cue {cue.index} ends before it starts")
        for word in cue.words:
            if word.start is None and word.end is None:
                continue
            if word.start is None or word.end is None:
                raise ValueError(f"Cue {cue.index} word {word.text!r} needs both a start and an end")
            if word.end < word.start:
                raise ValueError(f"Cue {cue.index} word {word.text!r} ends before it starts")
        if not cue.text:
            cue.text = " ".join(word.text for word in cue.words)
        cues.append(cue)
    if not cues:
        raise ValueError("The project has no subtitles")
    return Project(
        audio_path=str(raw["audio_path"]),
        srt_path=str(raw.get("srt_path", "")),
        audio_duration=float(raw.get("audio_duration") or 0),
        cues=cues,
        version=int(raw.get("version") or 1),
    )


def save_project(project: Project, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")


def load_project(path: Path) -> Project:
    return project_from_dict(json.loads(path.read_text(encoding="utf-8")))


def missing_timings(project: Project) -> int:
    return sum(1 for cue in project.cues for word in cue.words if word.start is None or word.end is None)


def flagged_words(project: Project) -> int:
    return sum(1 for cue in project.cues for word in cue.words if word.flagged)
