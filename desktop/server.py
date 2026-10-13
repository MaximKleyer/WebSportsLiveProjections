"""The desktop app's local web server.

Serves the built site (dist/) at the same path GitHub Pages does, its data
straight from public/data/ (so a fresh export shows without a rebuild), and
a small API for the app's run panel:

  GET  /api/status         every model's pass: plan, steps, progress
  GET  /api/log?sport=nfl  that model's latest run log (text)
  POST /api/run            {"sports": ["nfl"], "force": true} - start a pass
  POST /api/check          {"sport": "nfl"} - re-check that model's inputs now (its plan only)
  POST /api/dismiss        {"sport": "nfl", "id": "qbs", "undo"?: true} - a reminder marked Done
  POST /api/ping           a window is still open
  POST /api/bye            a window is closing (sent as a beacon)

It listens on 127.0.0.1 only, answers only to its own Host header, and a
POST must carry the X-Sports-Models header - which no page on another site
can send - so nothing else in the browser can start a run. (A beacon can't
carry headers, so /api/bye is exempt; it only counts if no ping follows.)
"""

from __future__ import annotations

import json
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist"
DATA = REPO / "public" / "data"
APP_ID = "sports-models-desktop"

# The site's base path - Vite's `base`, from REPO_NAME in src/config.js.
_repo = re.search(r"REPO_NAME\s*=\s*'([^']+)'", (REPO / "src" / "config.js").read_text(encoding="utf-8"))
BASE = f"/{_repo.group(1) if _repo else 'WebSportsLiveProjections'}/"

# Explicit, because Windows' registry can map .js to text/plain.
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
         ".woff2": "font/woff2", ".woff": "font/woff", ".txt": "text/plain; charset=utf-8",
         ".map": "application/json"}

# Tells the site it is running inside the app (the public site never has it).
MARKER = '<script>window.__SPORTS_DESKTOP__ = true;</script>'

SPLASH = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Sports Models</title>
<meta http-equiv="refresh" content="1"><style>
html,body{margin:0;height:100%;background:#0b0d11;color:#9aa0ac;font:500 13px/1.6 ui-monospace,monospace;
letter-spacing:.14em;text-transform:uppercase}body{display:grid;place-items:center}
b{color:#6ee7a0;font-weight:500}</style></head><body><p><b>&#9733;</b> {message}</p></body></html>"""


class App:
    """What the server needs from the rest of the app."""

    def __init__(self, runner, ui: dict):
        self.runner = runner
        self.ui = ui  # {"ready": bool, "building": bool, "error": str | None}
        self.windows: dict[str, float] = {}  # open window id -> when it last pinged
        self.seen_window = False


class Handler(BaseHTTPRequestHandler):
    app: App  # set on the class by serve()
    port: int
    server_version = "SportsModels"

    def log_message(self, format: str, *args) -> None:  # quiet: no line per request
        pass

    # --- routing --------------------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
        url = urlsplit(self.path)
        path = url.path
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", BASE)
            self.end_headers()
        elif path == "/api/status":
            self._json({"app": APP_ID, "now": int(time.time() * 1000), "ui": self.app.ui,
                        **self.app.runner.status()})
        elif path == "/api/log":
            sport = parse_qs(url.query).get("sport", [""])[0]
            self._send(HTTPStatus.OK, self.app.runner.log(sport).encode("utf-8"),
                       "text/plain; charset=utf-8", cache=False)
        elif path in (BASE, BASE + "index.html"):
            self._index()
        elif path.startswith(BASE + "data/"):
            self._file(DATA, path[len(BASE + "data/"):], cache=False)
        elif path.startswith(BASE):
            self._file(DIST, path[len(BASE):], cache=True)
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/bye" and self._host_ok():
            self.app.windows.pop(str(self._body().get("id")), None)
            return self._json({"ok": True})
        if not self._host_ok() or self.headers.get("X-Sports-Models") != "1":
            return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
        if path == "/api/ping":
            self.app.windows[str(self._body().get("id"))] = time.time()
            self.app.seen_window = True
            return self._json({"ok": True})
        if path == "/api/run":
            body = self._body()
            sports = body.get("sports") or None
            started = self.app.runner.start(sports, force=bool(body.get("force")))
            return self._json({"started": started})
        if path in ("/api/check", "/api/dismiss"):
            body = self._body()
            sport = str(body.get("sport"))
            if sport not in self.app.runner.sports:
                return self._send(HTTPStatus.NOT_FOUND, b"No such model", "text/plain")
            try:
                if path == "/api/check":
                    return self._json({"inputs": self.app.runner.check_inputs(sport)})
                self.app.runner.dismiss_input(sport, str(body.get("id")), undo=bool(body.get("undo")))
                return self._json({"ok": True})
            except Exception as error:  # a check that failed, an unknown item: say so, keep serving
                return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, str(error).encode("utf-8"),
                                  "text/plain; charset=utf-8")
        self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    # --- helpers --------------------------------------------------------------------------------

    def _host_ok(self) -> bool:
        return self.headers.get("Host") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            return {}

    def _index(self) -> None:
        if not self.app.ui.get("ready"):
            message = ("Couldn't build the app - see desktop/.state/app.log" if self.app.ui.get("error")
                       else "Getting the app ready&hellip;")
            return self._send(HTTPStatus.OK, SPLASH.replace("{message}", message).encode(), TYPES[".html"],
                              cache=False)
        html = (DIST / "index.html").read_text(encoding="utf-8")
        html = html.replace("</head>", MARKER + "</head>", 1)
        self._send(HTTPStatus.OK, html.encode("utf-8"), TYPES[".html"], cache=False)

    def _file(self, root: Path, rel: str, cache: bool) -> None:
        target = (root / rel).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
        kind = TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(HTTPStatus.OK, target.read_bytes(), kind, cache=cache and "/assets/" in target.as_posix())

    def _json(self, payload: dict) -> None:
        self._send(HTTPStatus.OK, json.dumps(payload).encode("utf-8"), TYPES[".json"], cache=False)

    def _send(self, status: HTTPStatus, body: bytes, kind: str, cache: bool = False) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            # Hashed assets never change; everything else must always be fresh.
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the window closed or navigated mid-response


def serve(app: App, port: int) -> ThreadingHTTPServer:
    """Start serving on 127.0.0.1:port in a background thread."""
    handler = type("BoundHandler", (Handler,), {"app": app, "port": port})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="http").start()
    return server
