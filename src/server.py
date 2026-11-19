"""Local page for loading a voiceover, correcting word timings, and exporting."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .align import align_project
from .config import CONFIG_PATH, ROOT, load_settings, save_settings, validate
from .layout import layout_project
from .media import media_duration
from .project import (
    load_project,
    missing_timings,
    project_from_dict,
    project_from_srt,
    replace_cue_sentence,
    save_project,
)
from .render import export_overlay, render_png

HOST = "127.0.0.1"
PORT = 8765
PROJECT_PATH = ROOT / "work" / "project.json"
WEB_ROOT = ROOT / "web"
FONT_PATH = ROOT / "assets" / "fonts" / "Montserrat-Bold.otf"


class Job:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.messages: list[str] = []
        self.error: str | None = None
        self.done: str | None = None
        self.export_path: str | None = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "messages": list(self.messages[-12:]),
                "error": self.error,
                "done": self.done,
                "export_path": self.export_path,
            }

    def log(self, message: str) -> None:
        print(message)
        with self._lock:
            self.messages.append(message)
            del self.messages[:-40]

    def start(self, kind: str, work) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("Wait for the current task to finish")
            self.running = True
            self.messages = []
            self.error = None
            self.done = None
            if kind == "export":
                self.export_path = None

        def runner():
            try:
                work(self.log)
                with self._lock:
                    self.done = kind
            except Exception as exc:
                self.log(f"Error: {exc}")
                with self._lock:
                    self.error = str(exc)
            finally:
                with self._lock:
                    self.running = False

        threading.Thread(target=runner, daemon=True).start()


JOB = Job()


def _safe_upload_name(name: str, fallback: str) -> str:
    base = Path(unquote(name or fallback)).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or fallback


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A003
        return

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route in {"/", "/index.html"}:
                self._send_bytes((WEB_ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif route == "/app.js":
                self._send_bytes((WEB_ROOT / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            elif route == "/app.css":
                self._send_bytes((WEB_ROOT / "app.css").read_bytes(), "text/css; charset=utf-8")
            elif route == "/font/Montserrat-Bold.otf":
                self._send_file(FONT_PATH, "font/otf")
            elif route == "/api/config":
                self._send_json(load_settings().to_dict())
            elif route == "/api/status":
                self._send_json(JOB.snapshot())
            elif route == "/api/project":
                if PROJECT_PATH.exists():
                    self._send_json({"project": json.loads(PROJECT_PATH.read_text(encoding="utf-8"))})
                else:
                    self._send_json({"project": None})
            elif route == "/api/layout":
                self._send_json(layout_project(load_project(PROJECT_PATH), load_settings()))
            elif route == "/api/audio":
                project = load_project(PROJECT_PATH)
                self._send_file(Path(project.audio_path))
            elif route == "/api/frame":
                query = parse_qs(parsed.query)
                time = float(query.get("t", ["0"])[0])
                self._send_bytes(render_png(load_project(PROJECT_PATH), load_settings(), time), "image/png")
            else:
                self._send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 400)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route == "/api/upload":
                self._upload(parse_qs(parsed.query))
            elif route == "/api/open":
                self._open_files(self._read_json())
            elif route == "/api/project":
                project = project_from_dict(self._read_json()["project"])
                save_project(project, PROJECT_PATH)
                self._send_json({"ok": True, "flagged": _flagged(project), "missing": missing_timings(project)})
            elif route == "/api/sentence":
                self._replace_sentence(self._read_json())
            elif route == "/api/config":
                settings = validate(self._read_json())
                save_settings(settings, CONFIG_PATH)
                self._send_json(settings.to_dict())
            elif route == "/api/layout":
                body = self._read_json()
                project = project_from_dict(body["project"]) if body.get("project") else load_project(PROJECT_PATH)
                self._send_json(layout_project(project, _settings_from_payload(body)))
            elif route == "/api/frame":
                body = self._read_json()
                project = project_from_dict(body["project"]) if body.get("project") else load_project(PROJECT_PATH)
                time = float(body.get("t") or 0)
                self._send_bytes(
                    render_png(project, _settings_from_payload(body), time),
                    "image/png",
                )
            elif route == "/api/align":
                self._start_align()
            elif route == "/api/export":
                self._start_export(self._read_json())
            else:
                self._send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 400)

    def _upload(self, query: dict) -> None:
        kind = (query.get("kind") or ["file"])[0]
        name = _safe_upload_name((query.get("name") or [""])[0], "audio.wav" if kind == "audio" else "subtitles.srt")
        folder = ROOT / "work" / "media"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        length = int(self.headers.get("Content-Length", "0"))
        remaining = length
        with target.open("wb") as handle:
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)
        self._send_json({"path": str(target), "kind": kind})

    def _open_files(self, body: dict) -> None:
        audio = Path(body["audio_path"]).expanduser()
        srt = Path(body["srt_path"]).expanduser()
        if not audio.is_file():
            raise FileNotFoundError(f"Audio file not found: {audio}")
        if not srt.is_file():
            raise FileNotFoundError(f"SRT file not found: {srt}")
        duration = media_duration(audio)
        project = project_from_srt(audio.resolve(), srt.resolve(), duration)
        save_project(project, PROJECT_PATH)
        self._send_json({"project": project.to_dict()})

    def _replace_sentence(self, body: dict) -> None:
        project = project_from_dict(body["project"])
        index = int(body["index"])
        cue = next((item for item in project.cues if item.index == index), None)
        if cue is None:
            raise ValueError(f"Cue {index} was not found")
        replace_cue_sentence(cue, str(body.get("sentence", "")))
        save_project(project, PROJECT_PATH)
        self._send_json({"project": project.to_dict()})

    def _start_align(self) -> None:
        if not PROJECT_PATH.exists():
            raise ValueError("Load an audio file and an SRT file first")

        def work(log):
            settings = load_settings()
            project = load_project(PROJECT_PATH)
            log(f"Using {Path(project.audio_path).name} and {len(project.cues)} cues")
            align_project(project, settings, log)
            save_project(project, PROJECT_PATH)
            log(f"Saved timings to {PROJECT_PATH.name}")

        JOB.start("align", work)
        self._send_json({"ok": True})

    def _start_export(self, body: dict) -> None:
        if not PROJECT_PATH.exists():
            raise ValueError("Load a project before exporting")
        requested = Path(body.get("output") or "output/subtitles.mov").expanduser()
        if not requested.is_absolute():
            requested = ROOT / requested
        if requested.suffix.lower() != ".mov":
            requested = requested.with_suffix(".mov")

        def work(log):
            settings = load_settings()
            project = load_project(PROJECT_PATH)
            log(f"Encoding {settings.width}×{settings.height} {settings.codec} at {settings.fps} fps")
            export_overlay(project, settings, requested, log)
            with JOB._lock:
                JOB.export_path = str(requested)

        JOB.start("export", work)
        self._send_json({"ok": True, "output": str(requested)})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path.name}")
        size = path.stat().st_size
        content_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() == ".wav":
            content_type = "audio/wav"
        elif path.suffix.lower() == ".mp3":
            content_type = "audio/mpeg"
        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if range_header and range_header.startswith("bytes="):
            spec = range_header.split("=", 1)[1]
            start_raw, _, end_raw = spec.partition("-")
            if start_raw:
                start = int(start_raw)
            if end_raw:
                end = int(end_raw)
            end = min(end, size - 1)
            if start > end or start < 0:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    break
                remaining -= len(chunk)


def _flagged(project) -> int:
    return sum(1 for cue in project.cues for word in cue.words if word.flagged)


def _settings_from_payload(body: dict):
    """Use draft settings from the page when previewing, otherwise the saved file."""
    raw = body.get("settings")
    if isinstance(raw, dict) and raw:
        return validate(raw)
    return load_settings()


def serve(open_browser: bool = True) -> None:
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        raise SystemExit(
            f"Could not start on {HOST}:{PORT} ({exc}). Close the other copy of this app and try again."
        ) from exc
    url = f"http://{HOST}:{PORT}"
    print(f"Subtitle generator is running at {url}", flush=True)
    print("Leave this window open while you use it. Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
