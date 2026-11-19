"""Build a voice with known gaps and check that word timings follow it.

Run from the project folder:

    .venv/bin/python tests/check_sync.py
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.align import align_project  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.media import media_duration  # noqa: E402
from src.project import project_from_srt  # noqa: E402


def run(command: list[str]) -> None:
    subprocess.check_call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def silence(path: Path, seconds: float) -> None:
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
        "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(path),
    ])


def spoken(path: Path, text: str) -> None:
    raw = path.with_suffix(".aiff")
    subprocess.check_call(["say", "-v", "Samantha", "-o", str(raw), text])
    run(["ffmpeg", "-y", "-i", str(raw), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(path)])


def concat(clips: list[Path], output: Path) -> None:
    command = ["ffmpeg", "-y"]
    for clip in clips:
        command.extend(["-i", str(clip)])
    inputs = "".join(f"[{index}:a]" for index in range(len(clips)))
    command.extend([
        "-filter_complex", f"{inputs}concat=n={len(clips)}:v=0:a=1[a]",
        "-map", "[a]", "-c:a", "pcm_s16le", str(output),
    ])
    run(command)


def overlap(start: float, end: float, window_start: float, window_end: float) -> float:
    return max(0.0, min(end, window_end) - max(start, window_start))


def main() -> None:
    folder = ROOT / "work" / "test" / "sync"
    folder.mkdir(parents=True, exist_ok=True)
    sentences = [["Welcome", "to", "the", "ocean"], ["Stay", "curious"]]
    gap = 0.45
    sentence_gap = 1.05
    clips: list[Path] = []
    windows: list[tuple[str, float, float, int]] = []
    cursor = 0.0

    def add_clip(path: Path) -> float:
        nonlocal cursor
        clips.append(path)
        length = duration(path)
        start = cursor
        cursor += length
        return start

    lead = folder / "lead.wav"
    silence(lead, 0.25)
    add_clip(lead)
    for sentence_index, sentence in enumerate(sentences):
        if sentence_index:
            bridge = folder / f"bridge-{sentence_index}.wav"
            silence(bridge, sentence_gap)
            add_clip(bridge)
        for word_index, text in enumerate(sentence):
            if word_index:
                pause = folder / f"pause-{sentence_index}-{word_index}.wav"
                silence(pause, gap)
                add_clip(pause)
            clip = folder / f"word-{sentence_index}-{word_index}.wav"
            spoken(clip, text)
            start = add_clip(clip)
            windows.append((text, start, start + duration(clip), sentence_index))
    tail = folder / "tail.wav"
    silence(tail, 0.25)
    add_clip(tail)

    audio = folder / "voice.wav"
    concat(clips, audio)
    cues = []
    for sentence_index, sentence in enumerate(sentences):
        group = [item for item in windows if item[3] == sentence_index]
        cues.append((group[0][1], group[-1][2], " ".join(sentence)))
    srt_lines = []
    for index, (start, end, text) in enumerate(cues, start=1):
        srt_lines.append(f"{index}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n")
    srt = folder / "voice.srt"
    srt.write_text("\n".join(srt_lines), encoding="utf-8")

    settings = load_settings()
    project = project_from_srt(audio, srt, media_duration(audio))
    align_project(project, settings, print)

    aligned = [word for cue in project.cues for word in cue.words]
    print("\nAligned:")
    for word in aligned:
        print(f"  {word.text:10} {word.start:.3f}–{word.end:.3f}  flagged={word.flagged}  {word.note}")
    if len(aligned) != len(windows):
        raise SystemExit(f"Expected {len(windows)} words, got {len(aligned)}")

    failures = []
    for word, (text, start, end, _) in zip(aligned, windows):
        if word.text != text:
            failures.append(f"{word.text!r} was expected to be {text!r}")
            continue
        covered = overlap(word.start, word.end, start, end)
        portion = covered / max(0.01, word.end - word.start)
        print(f"  overlap {text}: {portion:.0%} of the aligned word sits inside the spoken clip")
        if portion < 0.5:
            failures.append(f"{text} only overlaps its spoken clip by {portion:.0%}")
        if word.start < start - 0.2 or word.end > end + 0.2:
            failures.append(f"{text} timing {word.start:.3f}–{word.end:.3f} is outside {start:.3f}–{end:.3f}")

    first = project.cues[0].words
    inside_gaps = [first[index + 1].start - first[index].end for index in range(len(first) - 1)]
    between = project.cues[1].words[0].start - first[-1].end
    print(f"Gaps inside the first sentence: {[round(item, 3) for item in inside_gaps]}")
    print(f"Gap between sentences: {between:.3f}")
    if between <= max(inside_gaps) + 0.2:
        failures.append("The pause between sentences was not kept longer than the pauses inside a sentence")

    project_path = folder / "project.json"
    from src.project import save_project
    save_project(project, project_path)
    print(f"Saved {project_path}")
    if failures:
        raise SystemExit("Sync check failed:\n- " + "\n- ".join(failures))
    print("Sync check passed.")


def _stamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


if __name__ == "__main__":
    main()
