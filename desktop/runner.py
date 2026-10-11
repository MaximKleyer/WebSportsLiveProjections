"""Runs the models for the desktop app.

A pass, for each model side by side (separate folders, separate venvs):
  1. plan    desktop/sports/<id>.py plan - what's due now, and why
  2. steps   each due step in order, as `<id>.py step ...` (the kickoff freeze
             and the output checks happen in there)
  3. export  scripts/exporters/<id>.py - the model's CSVs -> public/data/<id>/
and once every model is done, scripts/site_export.py check over all the data.

What each run prints goes to desktop/.state/logs/; when each step last ran is
kept in desktop/.state/state.json, which the plans read. Standard library only:
this runs on the system Python, each model's code on its own venv's.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import tomllib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DESKTOP = REPO / "desktop"
STATE_DIR = DESKTOP / ".state"
STATE_FILE = STATE_DIR / "state.json"
LOGS = STATE_DIR / "logs"
SPORTS = DESKTOP / "sports"
EXPORTERS = REPO / "scripts" / "exporters"
SITE_EXPORT = REPO / "scripts" / "site_export.py"
KEEP_LOGS = 60  # per model

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashing up per command
log = logging.getLogger("app")
_ES_CONTINUOUS, _ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001  # SetThreadExecutionState flags


def _hold_awake(on: bool) -> bool:
    """Ask Windows to keep running instead of idling into sleep or Modern Standby - the display
    may still turn off, and the lid or power button still sleep it - or (`on` False) drop the
    ask. It belongs to the calling thread, and lapses with it. False if it couldn't be asked."""
    if os.name != "nt":
        return False
    import ctypes

    state = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if on else 0)
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(state))


def load_models() -> list[dict]:
    """desktop/models.toml, with `folder` and `python` as Paths."""
    with open(DESKTOP / "models.toml", "rb") as f:
        models = tomllib.load(f)["model"]
    for m in models:
        m["folder"] = Path(m["folder"])
        m["python"] = m["folder"] / m["python"]
    return models


def console_python() -> str:
    """python.exe beside the interpreter running this (pythonw.exe when started from the shortcut)."""
    exe = Path(sys.executable)
    sibling = exe.with_name("python.exe")
    return str(sibling if sibling.exists() else exe)


def ms(t: float | None) -> int | None:
    return None if t is None else int(t * 1000)


def iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


class Store:
    """desktop/.state/state.json: when each step last ran (the plans read this), and how each
    model's last pass ended (shown before the next one finishes)."""

    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("steps", {})
        self.data.setdefault("models", {})

    def record_step(self, sport: str, step: str, ok: bool, started: float, ended: float, summary: str) -> None:
        with self.lock:
            entry = self.data["steps"].setdefault(f"{sport}.{step}", {})
            entry["attempt"] = iso(started)
            if ok:
                entry["success"] = iso(ended)
            entry.update(ok=ok, summary=summary, seconds=round(ended - started, 1))
            self._save()

    def record_model(self, sport: str, last: dict) -> None:
        with self.lock:
            self.data["models"][sport] = last
            self._save()

    def model(self, sport: str) -> dict | None:
        return self.data["models"].get(sport)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)  # a plan reading it never sees half a file


@dataclass
class Step:
    id: str
    label: str
    args: list
    due: bool
    reason: str
    requires: list
    state: str = "pending"  # pending | running | done | failed | skipped
    summary: str = ""
    warnings: list = field(default_factory=list)
    started: float | None = None
    ended: float | None = None

    def snapshot(self) -> dict:
        return {"id": self.id, "label": self.label, "due": self.due, "reason": self.reason,
                "state": self.state, "summary": self.summary, "warnings": self.warnings,
                "started": ms(self.started), "ended": ms(self.ended)}


class SportRun:
    """One model's pass: its plan, its steps' progress, its log."""

    def __init__(self, model: dict, last: dict | None):
        self.model = model
        self.id, self.name = model["id"], model["name"]
        self.state = "idle"  # idle | planning | running | exporting | done | failed
        self.label, self.info, self.notes, self.steps = "", [], [], []
        self.error = ""
        self.forced = False
        self.current: str | None = None
        self.started = self.ended = None
        self.data_version = 0
        self.last = last  # how the previous pass ended (from state.json)
        self.log_path: Path | None = Path(last["log"]) if last and last.get("log") else None
        self.tail: deque[str] = deque(maxlen=800)
        self._log = None
        self.lock = threading.Lock()

    def begin(self, force: bool) -> None:
        with self.lock:
            self.state, self.forced, self.error = "planning", force, ""
            self.steps, self.notes, self.current = [], [], "plan"
            self.started, self.ended = time.time(), None
            self.tail.clear()
        LOGS.mkdir(parents=True, exist_ok=True)
        self.log_path = LOGS / f"{datetime.now():%Y%m%d-%H%M%S}-{self.id}.log"
        self._log = open(self.log_path, "w", encoding="utf-8")
        self.write(f"{self.name} - {'run now' if force else 'what is due'} - {datetime.now():%a %b %d %H:%M:%S}\n")
        logs = sorted(LOGS.glob(f"*-{self.id}.log"))
        for old in logs[:-KEEP_LOGS]:
            old.unlink(missing_ok=True)

    def write(self, text: str) -> None:
        if self._log:
            self._log.write(text)
            self._log.flush()
        for line in text.splitlines():
            self.tail.append(line)

    def end(self) -> dict:
        with self.lock:
            self.ended = time.time()
            self.current = None
            failed = [s for s in self.steps if s.state == "failed"]
            if self.state != "failed":
                self.state = "failed" if failed else "done"
            if failed and not self.error:
                self.error = f"{failed[0].label}: {failed[0].summary}"
        if self._log:
            self._log.close()
            self._log = None
        self.last = {"state": self.state, "label": self.label, "error": self.error,
                     "ended": ms(self.ended), "seconds": round(self.ended - self.started, 1),
                     "ran": [s.label for s in self.steps if s.state == "done"],
                     "summaries": [s.summary for s in self.steps if s.state in ("done", "failed")],
                     "log": str(self.log_path) if self.log_path else None}
        return self.last

    def snapshot(self) -> dict:
        with self.lock:
            return {"id": self.id, "name": self.name, "state": self.state, "label": self.label,
                    "info": self.info, "notes": self.notes, "error": self.error, "forced": self.forced,
                    "current": self.current, "started": ms(self.started), "ended": ms(self.ended),
                    "steps": [s.snapshot() for s in self.steps], "dataVersion": self.data_version,
                    "last": self.last}


class Runner:
    def __init__(self):
        self.store = Store()
        self.sports = {m["id"]: SportRun(m, self.store.model(m["id"])) for m in load_models()}
        self.threads: dict[str, threading.Thread] = {}
        self.checker: threading.Thread | None = None
        self.awake: threading.Thread | None = None  # keeps the PC from sleeping while a pass runs
        self.check = {"state": None, "summary": "", "at": None}
        self.lock = threading.Lock()

    # --- control --------------------------------------------------------------------------------

    def start(self, ids: list[str] | None = None, force: bool = False) -> list[str]:
        """Start a pass for these models (all by default). One that is mid-pass is left alone."""
        started = []
        with self.lock:
            for sid in ids or list(self.sports):
                if sid not in self.sports or self._alive(sid):
                    continue
                run = self.sports[sid]
                run.begin(force)
                thread = threading.Thread(target=self._pass, args=(run, force), daemon=True, name=f"run-{sid}")
                self.threads[sid] = thread
                thread.start()
                started.append(sid)
            if started and not (self.checker and self.checker.is_alive()):
                self.checker = threading.Thread(target=self._check_when_done, daemon=True, name="check")
                self.checker.start()
            if started and self.awake is None:
                self.awake = threading.Thread(target=self._stay_awake, daemon=True, name="awake")
                self.awake.start()
        return started

    def _stay_awake(self) -> None:
        """While any pass runs, keep the PC from dozing off: asleep, or in Modern Standby with the
        screen off, Windows freezes the models mid-run until it wakes (a 2-minute run once sat 19
        hours). Ends - and lets it sleep again - with the last pass."""
        held = _hold_awake(True)
        log.info("keeping the PC awake while the models run" if held else "couldn't ask Windows to stay awake")
        try:
            while True:
                time.sleep(2)
                with self.lock:  # start() checks `awake` under the same lock: no pass slips past
                    if not self.busy():
                        self.awake = None
                        return
        finally:
            _hold_awake(False)
            if held:
                log.info("the PC may sleep again")

    def busy(self) -> bool:
        return any(self._alive(sid) for sid in self.sports) or bool(self.checker and self.checker.is_alive())

    def wait(self) -> None:
        while self.busy():
            time.sleep(0.5)

    def _alive(self, sid: str) -> bool:
        thread = self.threads.get(sid)
        return bool(thread and thread.is_alive())

    def status(self) -> dict:
        return {"models": [run.snapshot() for run in self.sports.values()],
                "busy": self.busy(), "check": dict(self.check)}

    def log(self, sid: str, limit: int = 400_000) -> str:
        run = self.sports.get(sid)
        if run is None or run.log_path is None or not run.log_path.exists():
            return ""
        text = run.log_path.read_text(encoding="utf-8", errors="replace")
        return text[-limit:]

    # --- a model's pass -------------------------------------------------------------------------

    def _pass(self, run: SportRun, force: bool) -> None:
        try:
            plan = self._plan(run, force)
            with run.lock:
                run.label, run.info, run.notes = plan["label"], plan["info"], plan["notes"]
                run.steps = [Step(s["id"], s["label"], s["args"], s["due"], s["reason"], s["requires"])
                             for s in plan["steps"]]
                for step in run.steps:
                    if not step.due:
                        step.state = "skipped"
                run.state = "running"
            run.write("\nPlan:\n" + "".join(
                f"  {'run ' if s.due else 'skip'}  {s.label} - {s.reason}\n" for s in run.steps))
            for step in run.steps:
                if not step.due:
                    continue
                blocked = [r for r in step.requires if self._state_of(run, r) != "done"]
                if blocked:
                    step.state, step.summary = "skipped", f"waits for {self._label_of(run, blocked[0])}"
                    continue
                self._step(run, step)
            with run.lock:
                run.state, run.current = "exporting", "export"
            self._export(run)
        except Exception as error:  # a broken plan or exporter shouldn't take the app down
            with run.lock:
                run.state, run.error = "failed", str(error)
            run.write(f"\n{error}\n")
        finally:
            self.store.record_model(run.id, run.end())

    def _plan(self, run: SportRun, force: bool) -> dict:
        cmd = [run.model["python"], SPORTS / f"{run.id}.py", "plan", "--state", STATE_FILE]
        code, out = self._exec(run, cmd + (["--force"] if force else []), SPORTS)
        plan = out.get("plan")
        if code != 0 or plan is None:
            raise RuntimeError(f"Couldn't work out what to run ({out.get('last') or f'exit code {code}'})")
        return plan

    def _step(self, run: SportRun, step: Step) -> None:
        with run.lock:
            step.state, step.started, run.current = "running", time.time(), step.id
        cmd = [run.model["python"], SPORTS / f"{run.id}.py", "step", *step.args]
        code, out = self._exec(run, cmd, SPORTS)
        result = out.get("result") or {}
        ok = code == 0 and result.get("ok", True)
        with run.lock:
            step.ended = time.time()
            step.state = "done" if ok else "failed"
            step.summary = result.get("summary") or (f"exited with code {code}" if code else "done")
            step.warnings = result.get("warnings", [])
        self.store.record_step(run.id, step.id, ok, step.started, step.ended, step.summary)

    def _export(self, run: SportRun) -> None:
        exporter = EXPORTERS / f"{run.id}.py"
        if not exporter.exists():
            return
        code, out = self._exec(run, [run.model["python"], exporter], run.model["folder"])
        if code != 0:
            raise RuntimeError(f"Export to the site failed ({out.get('last') or f'exit code {code}'})")
        with run.lock:
            run.data_version += 1

    def _exec(self, run: SportRun, cmd: list, cwd: Path) -> tuple[int, dict]:
        """Run a command, streaming its output into the model's log. Returns (exit code,
        {plan, result: the @@ lines it printed; last: its last line of output})."""
        cmd = [str(c) for c in cmd]
        run.write(f"\n$ {subprocess.list2cmdline(cmd)}\n")
        # Unbuffered, so a model's progress shows in the log as it happens, not at the end.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
               "COLUMNS": "140"}
        out: dict = {}
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)
        except OSError as error:
            run.write(f"Couldn't start it: {error}\n")
            return 127, {"last": str(error)}
        for line in proc.stdout:
            if line.startswith("@@plan "):
                out["plan"] = json.loads(line[7:])
            elif line.startswith("@@result "):
                out["result"] = json.loads(line[9:])
            else:
                run.write(line)
                if line.strip():
                    out["last"] = line.strip()
        return proc.wait(), out

    @staticmethod
    def _state_of(run: SportRun, step_id: str) -> str | None:
        return next((s.state for s in run.steps if s.id == step_id), None)

    @staticmethod
    def _label_of(run: SportRun, step_id: str) -> str:
        return next((s.label for s in run.steps if s.id == step_id), step_id).lower()

    # --- the data check -------------------------------------------------------------------------

    def _check_when_done(self) -> None:
        """Once no model is mid-pass, validate every data file the site will read (again, if a
        pass started while the check ran)."""
        while True:
            while any(self._alive(sid) for sid in self.sports):
                time.sleep(0.5)
            self.check = {"state": "running", "summary": "", "at": None}
            try:
                proc = subprocess.run([console_python(), SITE_EXPORT, "check"], cwd=REPO, capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
                lines = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
                summary = lines[-1].strip() if lines else ""
                state = "ok" if proc.returncode == 0 else "failed"
            except OSError as error:
                state, summary = "failed", str(error)
            self.check = {"state": state, "summary": summary, "at": ms(time.time())}
            if not any(self._alive(sid) for sid in self.sports):
                return
