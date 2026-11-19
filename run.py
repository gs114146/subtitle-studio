"""Open the subtitle tool, or align and export from the command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402
from src.media import media_duration  # noqa: E402
from src.project import load_project, missing_timings, project_from_srt, save_project  # noqa: E402


def _align(audio: Path, srt: Path, out: Path, model: str | None) -> None:
    from src.align import align_project

    settings = load_settings()
    if model:
        settings.model = model
    project = project_from_srt(audio.resolve(), srt.resolve(), media_duration(audio))
    align_project(project, settings, print)
    save_project(project, out)
    missing = missing_timings(project)
    print(f"Saved {out}")
    if missing:
        print(f"{missing} words still need a start and end.")


def _export(project_path: Path, out: Path) -> None:
    from src.render import export_overlay

    project = load_project(project_path)
    export_overlay(project, load_settings(), out, print)


def main() -> None:
    parser = argparse.ArgumentParser(description="Word-by-word subtitle overlay for Shorts")
    sub = parser.add_subparsers(dest="command")

    align = sub.add_parser("align", help="Write word timings from an audio file and an SRT file")
    align.add_argument("--audio", required=True, type=Path)
    align.add_argument("--srt", required=True, type=Path)
    align.add_argument("--out", type=Path, default=ROOT / "work" / "project.json")
    align.add_argument("--model", default=None, help="Override the model in config.yaml for this run")

    export = sub.add_parser("export", help="Render a transparent subtitle movie from a timing file")
    export.add_argument("--project", required=True, type=Path)
    export.add_argument("--out", required=True, type=Path)

    serve = sub.add_parser("serve", help="Open the local page")
    serve.add_argument("--no-browser", action="store_true")

    engine = sub.add_parser("engine", help="JSON interface for the native Mac app")
    engine.add_argument("engine_args", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    if args.command == "align":
        _align(args.audio, args.srt, args.out, args.model)
    elif args.command == "export":
        _export(args.project, args.out)
    elif args.command == "engine":
        from src.engine_cli import main as engine_main

        engine_argv = list(args.engine_args)
        if engine_argv and engine_argv[0] == "--":
            engine_argv = engine_argv[1:]
        engine_main(engine_argv)
    else:
        from src.server import serve as serve_app

        serve_app(open_browser=not getattr(args, "no_browser", False))


if __name__ == "__main__":
    main()
