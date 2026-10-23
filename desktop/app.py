"""The Sports Models desktop app: double-click, a window opens on the latest
results, and the models that are due run in the background (runner.py).

  pythonw desktop\\app.py              what the desktop shortcut runs
  pythonw desktop\\app.py --background what the hourly scheduled task runs: what's due,
                                      no window, then exit (install-schedule.ps1)
  python desktop\\app.py --background --force
                                      every step of every model now, as Run everything now
                                      does (in the open app's window, if it's up)
  python desktop\\app.py --no-window   server only: open http://127.0.0.1:5190/
  python desktop\\app.py --no-run      open without running anything

The window is Microsoft Edge in app mode (no tabs or address bar) with its
own profile under %LOCALAPPDATA%\\SportsModels. Closing it closes the app -
after any model that is mid-run has finished, so nothing is left
half-written. Opening it again while it's still up just opens a new window.
Standard library only (Python 3.11+).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner import NO_WINDOW, REPO, STATE_DIR, Runner  # noqa: E402
from server import APP_ID, BASE, App, serve  # noqa: E402

DEFAULT_PORT = 5190
LOCK = STATE_DIR / "app.json"
APP_LOG = STATE_DIR / "app.log"
PROFILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SportsModels" / "edge-profile"
PING_TIMEOUT = 180     # s: a window that stops pinging is gone (a minimised one pings ~once a minute)
FIRST_PING_WAIT = 120  # s for the first window to start up
CLOSE_GRACE = 5        # s with no window open before the app closes (a reload reconnects in ~1)
BACKGROUND_GRACE = 10  # s a finished background run waits for a window that's starting up
UI_SOURCES = ("src", "index.html", "vite.config.js", "package.json", "public/favicon.svg")

log = logging.getLogger("app")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-window", action="store_true", help="serve only; don't open a window")
    ap.add_argument("--no-run", action="store_true", help="don't run the models on start")
    ap.add_argument("--background", action="store_true",
                    help="run what's due without a window, then exit (the hourly scheduled task)")
    ap.add_argument("--force", action="store_true",
                    help="run every step now, not only what's due (as Run everything now)")
    args = ap.parse_args()
    setup_logging()

    running = existing_app()
    if running and args.background:
        log.info("background run: the app is up on port %s, so it runs %s", running,
                 "everything" if args.force else "what's due")
        ask_to_run(running, args.force)
        return 0
    if running:
        log.info("already running on port %s; opening a window", running)
        if not args.no_window:
            open_window(f"http://127.0.0.1:{running}{BASE}")
        return 0

    ui = {"ready": False, "building": False, "error": None}
    runner = Runner()
    app = App(runner, ui)
    port = free_port(args.port)
    serve(app, port)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
    log.info("serving on http://127.0.0.1:%s%s", port, BASE)

    threading.Thread(target=prepare_ui, args=(ui,), daemon=True, name="ui").start()
    if not args.no_run:
        runner.start(force=args.force)
    try:
        if args.background:
            log.info("background run: running %s", "everything" if args.force else "what's due")
            runner.wait()
            if not window_joined(app):
                log.info("background run done")
                return 0
            # The app was opened meanwhile, its window on this server: stay up until it closes.
            edge = (PROFILE / "lockfile").exists()
        elif args.no_window:
            print(f"Sports Models: http://127.0.0.1:{port}{BASE}  (Ctrl+C to stop)", flush=True)
            while True:
                time.sleep(3600)
        else:
            edge = open_window(f"http://127.0.0.1:{port}{BASE}") is not None
        while True:
            wait_for_close(app, edge)
            if runner.busy():
                log.info("window closed mid-run; finishing the run first")
                runner.wait()
            if not window_open(app, edge):  # nobody opened it again meanwhile
                break
    except KeyboardInterrupt:
        pass
    finally:
        LOCK.unlink(missing_ok=True)
        log.info("closed")
    return 0


def setup_logging() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if APP_LOG.exists() and APP_LOG.stat().st_size > 1_000_000:
        APP_LOG.unlink()
    handlers = [logging.FileHandler(APP_LOG, encoding="utf-8")]
    if sys.stdout is not None:  # None under pythonw
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    sys.excepthook = lambda *exc: log.critical("crashed", exc_info=exc)


def existing_app() -> int | None:
    """The port of an app that's already up, if any."""
    try:
        port = json.loads(LOCK.read_text(encoding="utf-8"))["port"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
            return port if json.load(response).get("app") == APP_ID else None
    except (OSError, ValueError, KeyError):
        return None


def free_port(preferred: int) -> int:
    """The preferred port (a stable address keeps the window's saved settings), else any free one."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


# --- The window -----------------------------------------------------------------------------------

def find_edge() -> Path | None:
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if root:
            edge = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if edge.exists():
                return edge
    return None


def open_window(url: str) -> subprocess.Popen | None:
    """An app-mode Edge window on `url` (the default browser if there's no Edge)."""
    edge = find_edge()
    if edge is None:
        webbrowser.open(url)
        return None
    PROFILE.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([str(edge), f"--app={url}", f"--user-data-dir={PROFILE}", "--no-first-run",
                             "--no-default-browser-check", "--window-size=1440,960"])


def window_open(app: App, edge: bool) -> bool:
    return (PROFILE / "lockfile").exists() if edge else bool(app.windows)


def window_joined(app: App) -> bool:
    """After a background run: whether a window is open on this server (the app was opened
    mid-run) - watching a few seconds, in case one is just starting up."""
    deadline = time.time() + BACKGROUND_GRACE
    while True:
        if app.windows or (PROFILE / "lockfile").exists():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def ask_to_run(port: int, force: bool = False) -> None:
    """Have the app that's already up run what's due, or (`force`) everything; it shows the run
    in its window."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=json.dumps({"force": force}).encode(),
                                     method="POST",
                                     headers={"Content-Type": "application/json", "X-Sports-Models": "1"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            log.info("background run: the app started %s", json.load(response).get("started"))
    except (OSError, ValueError) as error:
        log.error("background run: couldn't reach the app: %s", error)


def wait_for_close(app: App, edge: bool) -> None:
    """Until no app window is open.

    msedge.exe hands each window to another process and exits at once, so it can't be
    waited on. But while any window is open on the app's profile, Edge holds the
    profile's `lockfile` (deleted when the last window closes) - so that's watched.
    Without Edge, the windows report in themselves: a ping while open, a goodbye on
    close (a reload says goodbye and pings again within a second, hence the grace)."""
    lock = PROFILE / "lockfile"
    if edge:
        deadline = time.time() + FIRST_PING_WAIT
        while not lock.exists() and time.time() < deadline:
            time.sleep(0.25)
        if lock.exists():
            while lock.exists():
                time.sleep(1)
            return
        log.warning("no Edge lockfile appeared; going by the window's pings")
    opened, empty_since = time.time(), None
    while True:
        time.sleep(1)
        now = time.time()
        for wid, seen in list(app.windows.items()):
            if now - seen > PING_TIMEOUT:  # closed without a goodbye (a crash, a kill)
                app.windows.pop(wid, None)
        if app.windows:
            empty_since = None
        elif not app.seen_window and now - opened < FIRST_PING_WAIT:
            continue  # the first window is still starting up
        else:
            empty_since = empty_since or now
            if now - empty_since >= CLOSE_GRACE:
                return


# --- The site build -------------------------------------------------------------------------------

def prepare_ui(ui: dict) -> None:
    """Rebuild dist/ if the site's source changed since the last build, then mark it ready."""
    built = REPO / "dist" / "index.html"
    stale = not built.exists() or newest_source() > built.stat().st_mtime
    if stale:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm is None:
            ui["error"] = "npm not found"
        else:
            ui["building"] = True
            log.info("building the site (src changed)")
            proc = subprocess.run([npm, "run", "build"], cwd=REPO, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            ui["building"] = False
            if proc.returncode != 0:
                log.error("build failed:\n%s%s", proc.stdout, proc.stderr)
                ui["error"] = "build failed"
    ui["ready"] = built.exists()


def newest_source() -> float:
    newest = 0.0
    for name in UI_SOURCES:
        path = REPO / name
        files = path.rglob("*") if path.is_dir() else [path]
        for f in files:
            if f.is_file():
                newest = max(newest, f.stat().st_mtime)
    return newest


if __name__ == "__main__":
    raise SystemExit(main())
