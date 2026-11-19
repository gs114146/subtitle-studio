"""When a word is highlighted, and how large the pop is.

The same rules are duplicated in web/app.js for the live preview.
"""

from __future__ import annotations

import math

from .project import WordTiming


def active_word_index(words: list[WordTiming], time: float, hold_seconds: float) -> int | None:
    """Return the reading-order index of the highlighted word at this time.

    A word stays highlighted through a short gap so the colour does not flicker.
    A longer pause clears the highlight until the next word starts.
    """
    usable = [
        (index, word)
        for index, word in enumerate(words)
        if word.start is not None and word.end is not None and word.end >= word.start
    ]
    if not usable:
        return None
    ordered = sorted(usable, key=lambda item: (item[1].start, item[0]))
    for position, (index, word) in enumerate(ordered):
        next_start = ordered[position + 1][1].start if position + 1 < len(ordered) else None
        if word.start <= time < word.end:
            return index
        # Keep the exact end instant on this word unless the next word starts then.
        if time == word.end and (next_start is None or next_start > time):
            return index

    for position, (index, word) in enumerate(ordered):
        if time < word.end:
            continue
        next_start = ordered[position + 1][1].start if position + 1 < len(ordered) else None
        if next_start is None:
            if time - word.end <= hold_seconds:
                return index
            return None
        if time < next_start and (next_start - word.end) <= hold_seconds:
            return index
        if time < next_start:
            return None
    return None


def pop_scale(time: float, word_start: float, animation_seconds: float, pop_scale_amount: float) -> float:
    """Scale rises and settles once, starting when the word itself starts."""
    if animation_seconds <= 0 or pop_scale_amount <= 1:
        return 1.0
    age = time - word_start
    if age < 0 or age > animation_seconds:
        return 1.0
    amount = math.sin(math.pi * age / animation_seconds)
    return 1.0 + (pop_scale_amount - 1.0) * amount


def visible_cue_index(cues, time: float) -> int | None:
    matches = [cue.index for cue in cues if cue.start <= time < cue.end]
    if matches:
        return matches[-1]
    ending = [cue.index for cue in cues if abs(cue.end - time) < 1e-6 and cue.start <= time]
    return ending[-1] if ending else None
