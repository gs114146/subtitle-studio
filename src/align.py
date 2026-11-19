"""Lock each SRT word to the voiceover with local forced alignment.

English cues are aligned with wav2vec2. The subtitle text stays as written.
The model only decides when each word is spoken, so pauses stay pauses.
"""

from __future__ import annotations

import os
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .config import ROOT, Settings
from .project import CueTiming, Project, WordTiming, normalize_token, refresh_cue_text

SAMPLE_RATE = 16000
_MODEL = None


def _log(progress, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message)


def load_audio(path: Path) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Could not read the voiceover. {detail}".strip())
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def load_model(settings: Settings, progress=None):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    if settings.language != "en":
        raise ValueError("This tool aligns English voiceovers. Set language to en in config.yaml.")

    os.environ.setdefault("TORCH_HOME", str(ROOT / "models" / "torch"))
    _log(progress, "Loading the speech model. The first run downloads it, then everything stays offline.")
    try:
        import torch
        import torchaudio

        torch.set_num_threads(settings.cpu_threads)
        bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        model = bundle.get_model()
        model.eval()
        labels = bundle.get_labels()
    except Exception as exc:
        raise RuntimeError(
            "The speech model could not be loaded. Connect to the internet once so it can download, "
            f"then try again. Details: {exc}"
        ) from exc
    _MODEL = (model, {label: index for index, label in enumerate(labels)})
    _log(progress, "Speech model is ready.")
    return _MODEL


def _word_tokens(text: str, dictionary: dict[str, int]) -> list[int]:
    tokens = []
    for character in text.upper():
        if character in dictionary and character not in {"-", "|"}:
            tokens.append(dictionary[character])
    return tokens


def _align_slice(model, dictionary: dict[str, int], samples: np.ndarray, words: list[str]) -> list[dict | None]:
    import torch
    from torchaudio.functional import forced_align, merge_tokens

    token_lists = [_word_tokens(word, dictionary) for word in words]
    pieces: list[int] = []
    usable = []
    separator = dictionary["|"]
    for index, tokens in enumerate(token_lists):
        if not tokens:
            continue
        if pieces:
            pieces.append(separator)
        pieces.extend(tokens)
        usable.append(index)
    timings: list[dict | None] = [None] * len(words)
    if not pieces or samples.size < SAMPLE_RATE * 0.05:
        return timings

    waveform = torch.from_numpy(np.ascontiguousarray(samples)).unsqueeze(0)
    with torch.inference_mode():
        emissions, _ = model(waveform)
        log_probs = torch.log_softmax(emissions, dim=-1)
        targets = torch.tensor([pieces], dtype=torch.int32)
        alignments, scores = forced_align(log_probs, targets, blank=0)
        spans = merge_tokens(alignments[0], scores[0])

    groups: list[list] = []
    current: list = []
    for span in spans:
        if span.token == separator:
            if current:
                groups.append(current)
                current = []
            continue
        current.append(span)
    if current:
        groups.append(current)
    if len(groups) != len(usable):
        raise RuntimeError(f"expected {len(usable)} words from alignment, got {len(groups)}")

    seconds_per_frame = samples.shape[0] / log_probs.shape[1] / SAMPLE_RATE
    for word_index, group in zip(usable, groups):
        probability = float(np.exp(np.mean([span.score for span in group])))
        timings[word_index] = {
            "text": words[word_index],
            "start": group[0].start * seconds_per_frame,
            "end": group[-1].end * seconds_per_frame,
            "probability": probability,
        }
    return timings


def _slice_bounds(cues: list[CueTiming], index: int, duration: float) -> tuple[float, float]:
    cue = cues[index]
    previous_end = cues[index - 1].end if index else 0.0
    next_start = cues[index + 1].start if index + 1 < len(cues) else duration
    pad_before = min(0.08, max(0.0, (cue.start - previous_end) / 2))
    pad_after = min(0.08, max(0.0, (next_start - cue.end) / 2))
    return max(0.0, cue.start - pad_before), min(duration, cue.end + pad_after)


def _tidy_aligned(words: list[dict]) -> list[dict]:
    """Drop punctuation-only tokens and absorb them into the previous word."""
    kept: list[dict] = []
    for word in words:
        text = word["text"].strip()
        if not text:
            continue
        if not normalize_token(text):
            if kept:
                kept[-1]["end"] = max(kept[-1]["end"], word["end"])
            continue
        if kept and normalize_token(kept[-1]["text"]) == normalize_token(text) and word["start"] <= kept[-1]["end"] + 0.02:
            kept[-1]["end"] = max(kept[-1]["end"], word["end"])
            if word.get("probability") is not None:
                kept[-1]["probability"] = word["probability"]
            continue
        kept.append({**word, "text": text})
    return kept


def _pair(original: list[str], aligned: list[dict]) -> list[int | None]:
    mapping: list[int | None] = [None] * len(original)
    if not original or not aligned:
        return mapping
    left = [normalize_token(text) for text in original]
    right = [normalize_token(word["text"]) for word in aligned]
    matcher = SequenceMatcher(a=left, b=right, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and (i2 - i1) == (j2 - j1)):
            for offset in range(i2 - i1):
                mapping[i1 + offset] = j1 + offset
    return mapping


def _interpolate(cue: CueTiming, words: list[WordTiming]) -> None:
    index = 0
    count = len(words)
    while index < count:
        if words[index].start is not None:
            index += 1
            continue
        end_gap = index
        while end_gap < count and words[end_gap].start is None:
            end_gap += 1
        previous_end = words[index - 1].end if index > 0 and words[index - 1].end is not None else cue.start
        next_start = words[end_gap].start if end_gap < count else cue.end
        slot_count = end_gap - index
        available = max(0.0, next_start - previous_end)
        if available < 0.05 * slot_count:
            available = 0.05 * slot_count
        weights = [max(1, len(normalize_token(words[pos].text))) for pos in range(index, end_gap)]
        total = float(sum(weights))
        cursor = previous_end
        for offset, weight in enumerate(weights):
            duration = available * weight / total
            words[index + offset].start = cursor
            words[index + offset].end = cursor + duration
            words[index + offset].flagged = True
            words[index + offset].note = "Timing was estimated. Listen and correct this word."
            cursor += duration
        index = end_gap


def _repair_overlaps(words: list[WordTiming]) -> None:
    previous_end = None
    for word in words:
        if word.start is None or word.end is None:
            continue
        if previous_end is not None and word.start < previous_end:
            word.start = previous_end
        if word.end < word.start:
            word.end = word.start + 0.04
            word.flagged = True
            if not word.note:
                word.note = "Timing was adjusted because the aligned duration was empty."
        previous_end = word.end


def _clamp_to_cue(cue: CueTiming, word: WordTiming) -> None:
    if word.start is None or word.end is None:
        return
    start = min(max(word.start, cue.start), cue.end)
    end = min(max(word.end, cue.start), cue.end)
    moved = abs(start - word.start) > 0.15 or abs(end - word.end) > 0.15
    word.start = start
    word.end = end
    if word.end < word.start:
        word.end = min(cue.end, word.start + 0.04)
    if moved:
        word.flagged = True
        if not word.note:
            word.note = "Timing sat outside this subtitle and was moved inside it."


def _build_cue_words(cue: CueTiming, aligned: list[dict], settings: Settings) -> list[WordTiming]:
    original = [word.text for word in cue.words] or cue.text.split()
    mapping = _pair(original, _tidy_aligned(aligned))
    built: list[WordTiming] = []
    tidy = _tidy_aligned(aligned)
    for index, text in enumerate(original):
        aligned_index = mapping[index]
        if aligned_index is None:
            built.append(WordTiming(text=text, flagged=True, note="This word was not locked to the audio."))
            continue
        heard = tidy[aligned_index]
        note = ""
        flagged = False
        if normalize_token(text) != normalize_token(heard["text"]):
            flagged = True
            note = f"Audio sounded like {heard['text']!r}. The subtitle text was kept."
        probability = heard.get("probability")
        if probability is not None and probability < settings.min_probability:
            flagged = True
            extra = f"Low confidence ({probability:.0%})."
            note = f"{note} {extra}".strip()
        built.append(
            WordTiming(
                text=text,
                start=heard["start"],
                end=heard["end"],
                probability=probability,
                flagged=flagged,
                note=note,
            )
        )
    _interpolate(cue, built)
    for word in built:
        _clamp_to_cue(cue, word)
    _repair_overlaps(built)
    return built


def _from_timings(cue: CueTiming, timings: list[dict | None], settings: Settings) -> list[WordTiming]:
    built: list[WordTiming] = []
    for word, timing in zip(cue.words, timings):
        if timing is None:
            built.append(WordTiming(text=word.text, flagged=True, note="This word was not locked to the audio."))
            continue
        probability = timing.get("probability")
        flagged = False
        note = ""
        if probability is not None and probability < settings.min_probability:
            flagged = True
            note = f"Low confidence ({probability:.0%}). Listen to this word."
        built.append(
            WordTiming(
                text=word.text,
                start=timing["start"],
                end=timing["end"],
                probability=probability,
                flagged=flagged,
                note=note,
            )
        )
    _interpolate(cue, built)
    for word in built:
        _clamp_to_cue(cue, word)
    _repair_overlaps(built)
    return built


def align_project(project: Project, settings: Settings, progress=None) -> Project:
    audio_path = Path(project.audio_path)
    refresh_cue_text(project)
    if not any(cue.text.strip() for cue in project.cues):
        raise ValueError("There is no subtitle text to align")
    samples = load_audio(audio_path)
    duration = samples.shape[0] / SAMPLE_RATE
    if project.audio_duration <= 0:
        project.audio_duration = duration
    model, dictionary = load_model(settings, progress)
    spoken = [cue for cue in project.cues if cue.text.strip()]
    _log(progress, f"Aligning {len(spoken)} subtitle cues to the voiceover…")

    for cue_number, cue in enumerate(spoken):
        _log(progress, f"Aligning cue {cue_number + 1} of {len(spoken)}")
        origin, stop = _slice_bounds(project.cues, project.cues.index(cue), duration)
        piece = samples[int(origin * SAMPLE_RATE) : int(stop * SAMPLE_RATE)]
        tokens = [word.text for word in cue.words] or cue.text.split()
        try:
            relative = _align_slice(model, dictionary, piece, tokens)
        except Exception as exc:
            _log(progress, f"Cue {cue.index} needs a manual check ({exc}).")
            relative = [None] * len(tokens)
        absolute: list[dict | None] = []
        for timing in relative:
            if timing is None:
                absolute.append(None)
                continue
            absolute.append({
                **timing,
                "start": timing["start"] + origin,
                "end": timing["end"] + origin,
            })
        cue.words = _from_timings(cue, absolute, settings)
        cue.text = " ".join(word.text for word in cue.words)
        preview = ", ".join(
            f"{word.text} ({word.start:.2f}–{word.end:.2f})"
            for word in cue.words
            if word.start is not None and word.end is not None
        )
        _log(progress, f"Cue {cue.index}: {preview}")

    flagged = sum(word.flagged for cue in project.cues for word in cue.words)
    total = sum(len(cue.words) for cue in project.cues)
    _log(progress, f"Word timings ready. {total} words, {flagged} marked for a check.")
    return project
