"""JSON command interface used by the native Mac app.

Every successful command prints one JSON object on stdout.
Progress lines go to stderr as {"type":"progress","message":"..."}.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import traceback
from pathlib import Path

from .align import align_project
from .config import CONFIG_PATH, ROOT, load_settings, save_settings, validate
from .layout import layout_project
from .media import ensure_media_path, media_duration, require_ffmpeg
from .project import (
    load_project,
    missing_timings,
    project_from_dict,
    project_from_srt,
    replace_cue_sentence,
    save_project,
)
from .render import export_overlay, render_png

PROJECT_PATH = ROOT / "work" / "project.json"


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _progress(message: str) -> None:
    sys.stderr.write(json.dumps({"type": "progress", "message": message}, ensure_ascii=False))
    sys.stderr.write("\n")
    sys.stderr.flush()


def _fail(message: str, code: int = 1) -> None:
    _emit({"ok": False, "error": message})
    raise SystemExit(code)


def _settings_from_arg(raw: str | None):
    if not raw:
        return load_settings()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("settings must be a JSON object")
    return validate(data)


def _load_or_fail() -> object:
    if not PROJECT_PATH.exists():
        _fail("No project loaded yet. Choose an audio file and an SRT file first.")
    return load_project(PROJECT_PATH)


def cmd_ping(_: argparse.Namespace) -> None:
    require_ffmpeg()
    python = Path(sys.executable)
    _emit(
        {
            "ok": True,
            "root": str(ROOT),
            "python": str(python),
            "config": str(CONFIG_PATH),
            "project_path": str(PROJECT_PATH),
            "has_project": PROJECT_PATH.exists(),
            "ffmpeg": True,
        }
    )


def cmd_get_config(_: argparse.Namespace) -> None:
    _emit({"ok": True, "settings": load_settings().to_dict()})


def cmd_set_config(args: argparse.Namespace) -> None:
    settings = _settings_from_arg(args.settings)
    save_settings(settings, CONFIG_PATH)
    _emit({"ok": True, "settings": settings.to_dict()})


def cmd_get_project(_: argparse.Namespace) -> None:
    if not PROJECT_PATH.exists():
        _emit({"ok": True, "project": None})
        return
    project = load_project(PROJECT_PATH)
    _emit({"ok": True, "project": project.to_dict()})


def cmd_open(args: argparse.Namespace) -> None:
    audio = Path(args.audio).expanduser().resolve()
    srt = Path(args.srt).expanduser().resolve()
    if not audio.is_file():
        _fail(f"Audio file not found: {audio}")
    if not srt.is_file():
        _fail(f"SRT file not found: {srt}")
    duration = media_duration(audio)
    project = project_from_srt(audio, srt, duration)
    save_project(project, PROJECT_PATH)
    _emit({"ok": True, "project": project.to_dict()})


def cmd_save_project(args: argparse.Namespace) -> None:
    raw = json.loads(args.project)
    project = project_from_dict(raw)
    save_project(project, PROJECT_PATH)
    _emit(
        {
            "ok": True,
            "project": project.to_dict(),
            "missing": missing_timings(project),
            "flagged": sum(1 for cue in project.cues for word in cue.words if word.flagged),
        }
    )


def cmd_sentence(args: argparse.Namespace) -> None:
    project = project_from_dict(json.loads(args.project))
    cue = next((item for item in project.cues if item.index == int(args.index)), None)
    if cue is None:
        _fail(f"Cue {args.index} was not found")
    replace_cue_sentence(cue, args.sentence)
    save_project(project, PROJECT_PATH)
    _emit({"ok": True, "project": project.to_dict()})


def cmd_align(_: argparse.Namespace) -> None:
    project = _load_or_fail()
    settings = load_settings()
    _progress(f"Using {Path(project.audio_path).name} and {len(project.cues)} cues")
    align_project(project, settings, _progress)
    save_project(project, PROJECT_PATH)
    _emit(
        {
            "ok": True,
            "project": project.to_dict(),
            "missing": missing_timings(project),
            "flagged": sum(1 for cue in project.cues for word in cue.words if word.flagged),
        }
    )


def cmd_layout(args: argparse.Namespace) -> None:
    if args.project:
        project = project_from_dict(json.loads(args.project))
    else:
        project = _load_or_fail()
    settings = _settings_from_arg(args.settings)
    _emit({"ok": True, "layout": layout_project(project, settings)})


def cmd_frame(args: argparse.Namespace) -> None:
    if args.project:
        project = project_from_dict(json.loads(args.project))
    else:
        project = _load_or_fail()
    settings = _settings_from_arg(args.settings)
    png = render_png(project, settings, float(args.t))
    encoded = base64.b64encode(png).decode("ascii")
    _emit({"ok": True, "png_base64": encoded, "t": float(args.t)})


def cmd_export(args: argparse.Namespace) -> None:
    project = _load_or_fail()
    settings = load_settings()
    output = Path(args.out).expanduser()
    if not output.is_absolute():
        output = ROOT / output
    if output.suffix.lower() != ".mov":
        output = output.with_suffix(".mov")
    _progress(f"Encoding {settings.width}×{settings.height} {settings.codec} at {settings.fps} fps")
    export_overlay(project, settings, output, _progress)
    _emit({"ok": True, "export_path": str(output)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Native Mac app engine")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping")
    sub.add_parser("get_config")
    set_config = sub.add_parser("set_config")
    set_config.add_argument("--settings", required=True)

    sub.add_parser("get_project")
    open_cmd = sub.add_parser("open")
    open_cmd.add_argument("--audio", required=True)
    open_cmd.add_argument("--srt", required=True)

    save = sub.add_parser("save_project")
    save.add_argument("--project", required=True)

    sentence = sub.add_parser("sentence")
    sentence.add_argument("--project", required=True)
    sentence.add_argument("--index", required=True, type=int)
    sentence.add_argument("--sentence", required=True)

    sub.add_parser("align")

    layout = sub.add_parser("layout")
    layout.add_argument("--project", default=None)
    layout.add_argument("--settings", default=None)

    frame = sub.add_parser("frame")
    frame.add_argument("--t", required=True, type=float)
    frame.add_argument("--project", default=None)
    frame.add_argument("--settings", default=None)

    export = sub.add_parser("export")
    export.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    ensure_media_path()
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "ping": cmd_ping,
        "get_config": cmd_get_config,
        "set_config": cmd_set_config,
        "get_project": cmd_get_project,
        "open": cmd_open,
        "save_project": cmd_save_project,
        "sentence": cmd_sentence,
        "align": cmd_align,
        "layout": cmd_layout,
        "frame": cmd_frame,
        "export": cmd_export,
    }
    try:
        handlers[args.command](args)
    except SystemExit:
        raise
    except Exception as exc:
        detail = f"{exc}"
        if not detail:
            detail = traceback.format_exc().strip().splitlines()[-1]
        _emit({"ok": False, "error": detail})
        raise SystemExit(1) from exc
