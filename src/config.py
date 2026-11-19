"""Style and alignment settings stored in config.yaml."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

FALLBACK_FONTS = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
)


@dataclass
class Settings:
    model: str = "base.en"
    language: str = "en"
    cpu_threads: int = 4
    min_probability: float = 0.2
    font_path: str = "assets/fonts/Montserrat-Bold.otf"
    font_size: int = 76
    color_normal: str = "#FFFFFF"
    color_highlight: str = "#FFD43B"
    shadow_opacity: float = 0.55
    shadow_offset: int = 3
    margin_bottom: int = 220
    bottom_fraction: float = 0.30
    max_width_ratio: float = 0.86
    line_spacing: float = 1.18
    animation_ms: int = 140
    pop_scale: float = 1.08
    pause_hold_ms: int = 220
    orientation: str = "portrait"
    position: str = "bottom"
    width: int = 1080
    height: int = 1920
    fps: int = 30
    codec: str = "prores4444"

    def to_dict(self) -> dict:
        return asdict(self)


def hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6 or any(char not in "0123456789abcdefABCDEF" for char in text):
        raise ValueError(f"Colour must look like #FFD43B (got {value!r})")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), alpha)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(data: dict) -> Settings:
    known = set(Settings.__dataclass_fields__)
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(unknown)}")
    merged = Settings().to_dict()
    merged.update(data)
    item = Settings(**merged)

    _check(item.model.strip() != "", "Model name is empty")
    _check(item.language.isalpha() and 2 <= len(item.language) <= 8, "Language should be a code such as en")
    if item.model.endswith(".en") and item.language.lower() != "en":
        raise ValueError(
            f"Model {item.model} only aligns English. Use model 'small' or 'base' for {item.language}."
        )
    _check(1 <= item.cpu_threads <= 8, "CPU threads must be from 1 to 8")
    _check(0 < item.min_probability < 1, "Minimum probability must be between 0 and 1")
    _check(24 <= item.font_size <= 180, "Font size must be from 24 to 180")
    hex_to_rgba(item.color_normal)
    hex_to_rgba(item.color_highlight)
    _check(0 <= item.shadow_opacity <= 1, "Shadow opacity must be from 0 to 1")
    _check(0 <= item.shadow_offset <= 16, "Shadow offset must be from 0 to 16")
    _check(0 <= item.margin_bottom <= item.height // 2, "Edge margin is too large for this frame")
    _check(0.15 <= item.bottom_fraction <= 0.5, "Zone fraction must be from 0.15 to 0.50")
    _check(0.4 <= item.max_width_ratio <= 0.98, "Max width ratio must be from 0.40 to 0.98")
    _check(1.0 <= item.line_spacing <= 2.0, "Line spacing must be from 1.0 to 2.0")
    _check(0 <= item.animation_ms <= 800, "Animation duration must be from 0 to 800 ms")
    _check(1.0 <= item.pop_scale <= 1.25, "Pop scale must be from 1.00 to 1.25")
    _check(0 <= item.pause_hold_ms <= 1500, "Pause hold must be from 0 to 1500 ms")
    orientation = str(item.orientation or "portrait").strip().lower()
    position = str(item.position or "bottom").strip().lower()
    _check(orientation in {"portrait", "landscape"}, "Orientation must be portrait or landscape")
    _check(position in {"top", "middle", "bottom"}, "Position must be top, middle, or bottom")
    _check(item.width % 2 == 0 and item.height % 2 == 0, "Width and height must be even numbers")
    _check(item.width >= 320 and item.height >= 320, "Frame size is too small")
    _check(1 <= item.fps <= 60, "Frame rate must be from 1 to 60")
    _check(item.codec in {"prores4444", "qtrle"}, "Codec must be prores4444 or qtrle")
    item.language = item.language.lower()
    item.orientation = orientation
    item.position = position
    return item


def load_settings(path: Path | None = None) -> Settings:
    target = path or CONFIG_PATH
    if not target.exists():
        return Settings()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{target} must contain settings, not a list")
    return validate(raw)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    target = path or CONFIG_PATH
    header = (
        "# Reused for every Short.\n"
        "# language: en. Word timing uses a local English speech model.\n"
        "# codec: prores4444, or qtrle if Resolve shows a black background.\n"
    )
    body = yaml.safe_dump(settings.to_dict(), sort_keys=False, allow_unicode=True)
    target.write_text(header + body, encoding="utf-8")


def resolve_font(settings: Settings) -> Path:
    requested = Path(settings.font_path)
    if not requested.is_absolute():
        requested = ROOT / requested
    if requested.exists():
        return requested
    for candidate in FALLBACK_FONTS:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Font not found: {settings.font_path}. Set font_path in config.yaml to a .ttf or .otf file."
    )
