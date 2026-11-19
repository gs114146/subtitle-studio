"""Small helpers around ffprobe."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_HOMEBREW_BINARIES = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def ensure_media_path() -> None:
    """GUI apps often miss Homebrew on PATH; prepend common locations once."""
    extras = [str(path) for path in _HOMEBREW_BINARIES if path.is_dir()]
    if not extras:
        return
    current = os.environ.get("PATH", "")
    parts = [part for part in current.split(":") if part]
    merged = extras + [part for part in parts if part not in extras]
    os.environ["PATH"] = ":".join(merged)


def ffmpeg_bin(name: str) -> str:
    ensure_media_path()
    found = shutil.which(name)
    if found:
        return found
    for folder in _HOMEBREW_BINARIES:
        candidate = folder / name
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        f"{name} is required but was not found on PATH. "
        "Install it with Homebrew: brew install ffmpeg"
    )


def require_ffmpeg() -> None:
    ffmpeg_bin("ffmpeg")
    ffmpeg_bin("ffprobe")


def media_duration(path: Path) -> float:
    require_ffmpeg()
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    result = subprocess.run(
        [
            ffmpeg_bin("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"Could not read the audio duration: {detail}")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not read the audio duration of {path.name}") from exc
    if duration <= 0:
        raise RuntimeError(f"The audio file has no duration: {path.name}")
    return duration
