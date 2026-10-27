"""Local web UI: `cliptool ui`. Standard library only; listens on 127.0.0.1.

Safety: a random token per run must come with every API call, the Host header must be
localhost (blocks DNS rebinding), and a cross-site Origin is refused. Files are served only
from a project's own folder or its source video.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import subprocess
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import APP_NAME, __version__, styles, titles
from ..cli import FONTS_DIR
from ..transcript import to_dict
from . import actions
from .jobs import Busy, Jobs
from .projects import Store

log = logging.getLogger("cliptool")
STATIC = Path(__file__).resolve().parent / "static"
MAX_JSON = 20_000_000
CHUNK = 1 << 20
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/vtt", ".vtt")
mimetypes.add_type("text/plain", ".srt")
mimetypes.add_type("text/plain", ".ass")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("font/ttf", ".ttf")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _content_length(headers: Any) -> int:
    try:
        return max(0, int(headers.get("Content-Length") or 0))
    except ValueError:
        return 0


class _CountingReader:
    """Wraps the request body so a refused upload knows how much is still unread."""

    def __init__(self, raw: Any):
        self.raw, self.count = raw, 0

    def read(self, n: int = -1) -> bytes:
        data = self.raw.read(n)
        self.count += len(data)
        return data


class App:
    def __init__(self, workspace: Path, port: int) -> None:
        self.store = Store(workspace)
        self.jobs = Jobs()
        self.token = secrets.token_urlsafe(24)
        self.port = port

    def project(self, pid: str):
        try:
            return self.store.get(pid)
        except KeyError:
            raise ApiError(404, "No such project.") from None


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    routes: list[tuple[str, re.Pattern, str]] = [
        ("GET", re.compile(r"^/$"), "index"),
        ("GET", re.compile(r"^/static/([\w.-]+)$"), "static"),
        ("GET", re.compile(r"^/fonts/([\w.-]+\.ttf)$"), "font"),
        ("GET", re.compile(r"^/api/state$"), "state"),
        ("POST", re.compile(r"^/api/projects$"), "open_path"),
        ("PUT", re.compile(r"^/api/upload$"), "upload"),
        ("GET", re.compile(r"^/api/projects/([\w-]+)$"), "detail"),
        ("GET", re.compile(r"^/api/projects/([\w-]+)/transcript$"), "get_transcript"),
        ("PUT", re.compile(r"^/api/projects/([\w-]+)/transcript$"), "put_transcript"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/replace$"), "replace"),
        ("GET", re.compile(r"^/api/projects/([\w-]+)/preview$"), "preview"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/transcribe$"), "transcribe"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/burn$"), "burn"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/find$"), "find"),
        ("GET", re.compile(r"^/api/projects/([\w-]+)/judge$"), "judge"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/ranking$"), "ranking"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/judge-llm$"), "judge_llm"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/ai/(\w+)/(prompt|apply|ask)$"), "ai"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/render$"), "render"),
        ("POST", re.compile(r"^/api/projects/([\w-]+)/reveal$"), "reveal"),
        ("GET", re.compile(r"^/api/projects/([\w-]+)/waveform$"), "waveform"),
        ("GET", re.compile(r"^/api/jobs/(\w+)$"), "job"),
        ("POST", re.compile(r"^/api/jobs/(\w+)/cancel$"), "cancel"),
        ("GET", re.compile(r"^/media/([\w-]+)/source$"), "media_source"),
        ("GET", re.compile(r"^/media/([\w-]+)/file/(.+)$"), "media_file"),
        ("GET", re.compile(r"^/media/([\w-]+)/strip$"), "media_strip"),
    ]
    public = {"index", "static", "font"}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"{APP_NAME}/{__version__}"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
            log.debug("%s %s", self.address_string(), fmt % args)

        # --- plumbing -------------------------------------------------------
        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            self.query = parse_qs(url.query)
            self._body_read = False
            try:
                self._check_host_and_origin(method)
                for m, pattern, name in routes:
                    match = pattern.match(unquote(url.path)) if m == method else None
                    if match:
                        if name not in public:
                            self._check_token()
                        getattr(self, f"h_{name}")(*match.groups())
                        return
                raise ApiError(404, "Not found.")
            except ApiError as e:
                self._error({"error": str(e)}, e.status)
            except Busy as e:
                self._error({"error": str(e)}, 409)
            except (ValueError, KeyError) as e:
                self._error({"error": str(e).strip("'")}, 400)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the browser stopped reading (normal when seeking video)
            except Exception as e:  # never leak a traceback to the page
                log.exception("Request failed: %s %s", method, self.path)
                self._error({"error": f"Internal error: {type(e).__name__}"}, 500)

        def _error(self, data: dict, status: int) -> None:
            # Windows resets a socket closed with unread request data, and the client then never
            # sees this response. Read a small unread body first; drop the connection after a big one.
            if not self._body_read:
                self._body_read = True
                length = _content_length(self.headers)
                if 0 < length <= MAX_JSON:
                    self.rfile.read(length)
                elif length:
                    self.close_connection = True
            self._json(data, status)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def _check_host_and_origin(self, method: str) -> None:
            allowed = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}
            if self.headers.get("Host", "") not in allowed:
                raise ApiError(403, "Bad Host header.")
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed}:
                raise ApiError(403, "Cross-site request refused.")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ApiError(403, "Cross-site request refused.")

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")  # media URLs carry the token
            super().end_headers()

        def _check_token(self) -> None:
            given = self.headers.get("X-Cliptool-Token") or (self.query.get("t") or [""])[0]
            if not secrets.compare_digest(given, app.token):
                raise ApiError(403, "Missing or wrong session token. Reload the page.")

        def _body_json(self) -> Any:
            length = _content_length(self.headers)
            if length > MAX_JSON:
                raise ApiError(413, "Request too large.")
            self._body_read = True
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as e:
                raise ApiError(400, f"Bad JSON: {e}") from None

        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, cache: bool = False) -> None:
            """Serve a file, honouring a single byte Range so <video> can seek."""
            size = path.stat().st_size
            start, end = 0, size - 1
            rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
            status = 200
            if rng and size:
                a, b = rng.groups()
                if a:
                    start, end = int(a), min(int(b), size - 1) if b else size - 1
                elif b:
                    start = max(0, size - int(b))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        # --- pages and assets -------------------------------------------------
        def h_index(self) -> None:
            html = (STATIC / "index.html").read_text(encoding="utf-8")
            body = html.replace("{{TOKEN}}", app.token).replace("{{VERSION}}", __version__).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
                             "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def h_static(self, name: str) -> None:
            path = STATIC / name
            if not path.is_file():
                raise ApiError(404, "Not found.")
            self._file(path)

        def h_font(self, name: str) -> None:
            path = FONTS_DIR / name
            if not path.is_file():
                raise ApiError(404, "Not found.")
            self._file(path, cache=True)

        # --- API ----------------------------------------------------------------
        def h_state(self) -> None:
            presets = {n: json.loads((styles.PRESETS_DIR / f"{n}.json").read_text(encoding="utf-8"))
                       for n in styles.available()}
            self._json({"version": __version__, "workspace": str(app.store.root), "styles": presets,
                        "title_looks": list(titles.LOOKS), "emphasis": list(styles.EMPHASIS),
                        "projects": [actions.summary(app.store, p) for p in app.store.all()],
                        "jobs": [asdict(j) for j in app.jobs.active()]})

        def h_open_path(self) -> None:
            p = app.store.from_path(str(self._body_json().get("path", "")))
            self._json(actions.summary(app.store, p))

        def h_upload(self) -> None:
            name = (self.query.get("name") or [""])[0]
            length = _content_length(self.headers)
            body = _CountingReader(self.rfile)
            self._body_read = True  # handled here: a refused upload drains or closes below
            try:
                p = app.store.from_upload(name, body, length)
            except ValueError:
                # Windows resets a socket closed with unread data, so the client never sees the
                # 400. Drain a small refused body first; a big one just closes the connection.
                self.close_connection = True
                left = length - body.count
                if 0 < left <= 8 << 20:
                    body.read(left)
                raise
            self._json(actions.summary(app.store, p))

        def h_detail(self, pid: str) -> None:
            self._json(actions.detail(app.store, app.project(pid)))

        def h_get_transcript(self, pid: str) -> None:
            self._json(to_dict(actions.load_transcript(app.store, app.project(pid))))

        def h_put_transcript(self, pid: str) -> None:
            t = actions.save_transcript(app.store, app.project(pid), self._body_json())
            self._json(to_dict(t))

        def h_replace(self, pid: str) -> None:
            pairs = self._body_json().get("pairs") or []
            if not isinstance(pairs, list):
                raise ApiError(400, "pairs must be a list of OLD=NEW strings.")
            self._json(to_dict(actions.replace_words(app.store, app.project(pid), [str(x) for x in pairs])))

        def h_preview(self, pid: str) -> None:
            style = self._style((self.query.get("style") or ["bold"])[0])
            emphasis = (self.query.get("emphasis") or ["auto"])[0]
            self._json(actions.preview_chunks(app.store, app.project(pid), style, emphasis))

        def _style(self, value: Any) -> str:
            name = str(value or "bold")
            if name not in styles.available():  # the UI never needs a style file path
                raise ApiError(400, f"Unknown style {name!r}.")
            return name

        def _submit(self, kind: str, pid: str, work: Any) -> None:
            self._json(asdict(app.jobs.submit(kind, pid, work)))

        def h_transcribe(self, pid: str) -> None:
            b = self._body_json()
            replace = [str(x) for x in (b.get("replace") or []) if str(x).strip()]
            self._submit("transcribe", pid, actions.job_transcribe(
                app.store, app.project(pid), b.get("language"), b.get("prompt"), replace))

        def h_burn(self, pid: str) -> None:
            b = self._body_json()
            self._submit("burn", pid, actions.job_burn(app.store, app.project(pid), self._style(b.get("style")),
                                                       str(b.get("emphasis", "auto"))))

        def h_find(self, pid: str) -> None:
            b = self._body_json()
            self._submit("find", pid, actions.job_find(app.store, app.project(pid),
                                                       float(b.get("min", 20)), float(b.get("max", 60))))

        def h_judge(self, pid: str) -> None:
            count = int((self.query.get("count") or ["5"])[0])
            p = app.project(pid)
            prompt = actions.judge_prompt(app.store, p, max(1, min(count, 20)))
            ps = actions.paths(app.store, p)
            ps["judge"].write_text(prompt, encoding="utf-8")  # so a local AI agent can read it from disk
            self._json({"prompt": prompt, "judge_path": str(ps["judge"]), "ranking_path": str(ps["ranking"])})

        def h_ranking(self, pid: str) -> None:
            b = self._body_json()
            entries = b.get("entries")
            if b.get("from_file"):  # an AI agent saved its answer in the project folder
                from ..clipfind import load_ranking
                path = actions.paths(app.store, app.project(pid))["ranking"]
                if not path.is_file():
                    raise ApiError(404, f"No ranking.json yet in {path.parent}.")
                entries = load_ranking(path)
            elif entries is None:  # pasted LLM reply
                from ..clipfind import load_ranking_text
                entries = load_ranking_text(str(b.get("text", "")))
            self._json(actions.apply_selection(app.store, app.project(pid), entries, int(b.get("count", 50))))

        def h_judge_llm(self, pid: str) -> None:
            b = self._body_json()
            count = max(1, min(int(b.get("count", 5)), 20))
            self._submit("judge", pid, actions.job_judge_llm(app.store, app.project(pid), str(b.get("url", "")),
                                                             str(b.get("model", "")), count))

        def h_ai(self, pid: str, task: str, action: str) -> None:
            b = self._body_json()
            p = app.project(pid)
            count = max(1, min(int(b.get("count", 5)), 20))
            clips = b.get("clips") if isinstance(b.get("clips"), list) else None
            if action == "prompt":
                self._json(actions.ai_prompt(app.store, p, task, count, clips))
            elif action == "apply":
                if b.get("from_file"):
                    try:
                        entries = actions.ai_reply_from_file(app.store, p, task)
                    except FileNotFoundError as e:
                        raise ApiError(404, str(e)) from None
                elif "entries" in b:
                    entries = b["entries"]
                else:
                    from ..clipfind import load_ranking_text
                    entries = load_ranking_text(str(b.get("text", "")), "The AI's reply")
                self._json(actions.ai_apply(app.store, p, task, entries, count, clips))
            else:  # ask: the key (if any) lives only in this request and the job's memory
                key = str(b.get("key") or "") or None
                self._submit("ai", pid, actions.job_ai(app.store, p, task, str(b.get("url", "")),
                                                       str(b.get("model", "")), key, count, clips))

        def h_render(self, pid: str) -> None:
            b = self._body_json()
            entries = b.get("clips") or []
            if not entries:
                raise ApiError(400, "Pick at least one clip.")
            self._submit("render", pid, actions.job_render(
                app.store, app.project(pid), entries, self._style(b.get("style")), str(b.get("layout", "auto")),
                str(b.get("emphasis", "auto")), str(b.get("title_look", "banner")), float(b.get("title_secs", 3.5))))

        def h_waveform(self, pid: str) -> None:
            self._json(actions.waveform(app.store, app.project(pid)))

        def h_reveal(self, pid: str) -> None:
            folder = app.store.folder(app.project(pid).id)
            sub = str(self._body_json().get("sub", ""))
            target = folder / sub if sub in ("", "clips") else folder
            target.mkdir(exist_ok=True)  # "clips" may not exist before the first render
            if sys.platform == "win32":
                os.startfile(target)  # opens Explorer on the user's own PC
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])
            self._json({"ok": True})

        def h_cancel(self, job_id: str) -> None:
            try:
                self._json(asdict(app.jobs.cancel(job_id)))
            except KeyError:
                raise ApiError(404, "No such job.") from None

        def h_job(self, job_id: str) -> None:
            try:
                self._json(asdict(app.jobs.get(job_id)))
            except KeyError:
                raise ApiError(404, "No such job.") from None

        def h_media_source(self, pid: str) -> None:
            self._file(Path(app.project(pid).source))

        def h_media_file(self, pid: str, rel: str) -> None:
            app.project(pid)
            try:
                self._file(app.store.file(pid, rel))
            except KeyError:
                raise ApiError(404, "No such file.") from None

        def h_media_strip(self, pid: str) -> None:
            raw = (self.query.get("at") or [""])[0]
            try:
                times = [float(x) for x in raw.split(",") if x]
            except ValueError:
                raise ApiError(400, "at must be comma-separated seconds.") from None
            height = int((self.query.get("h") or ["120"])[0])
            self._file(actions.strip(app.store, app.project(pid), times, max(40, min(height, 360))), cache=True)

    return Handler


def serve(workspace: Path, port: int = 8765, open_browser: bool = True) -> None:
    app = App(workspace, port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    log.info(f"{APP_NAME} is running at %s  (workspace: %s)\nPress Ctrl+C to stop.", url, app.store.root)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
