"""Shared pieces of the desktop app's sport modules (desktop/sports/<id>.py).

A sport module runs with its MODEL's venv Python (pandas is there), the way
the site's exporters do, and answers two commands:

  plan  Print what the app should run now, and why, as one "@@plan {json}"
        line. Nothing runs. Reads the app's run history (--state) to see
        when each step last ran.
  step  Run one model command, protected: back up the slate file it
        rewrites, run it, then put back every game that kicked off before
        the run started (the kickoff freeze) and check what came out.
        Ends with one "@@result {json}" line.

desktop/app.py calls both. scripts/update-data.ps1 calls `step`, so a manual
run is protected the same way.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

DESKTOP = Path(__file__).resolve().parent.parent
STATE_FILE = DESKTOP / ".state" / "state.json"
BACKUPS = DESKTOP / ".state" / "backups"
PENDING = DESKTOP / ".state" / "pending"  # freezes a step died before settling (see Freeze)
KEEP_BACKUPS_DAYS = 45
STALE_PENDING = timedelta(hours=6)  # recover a note this old even if its pids look alive (reused)
ET = ZoneInfo("America/New_York")


def model_entry(sport: str) -> dict:
    """This sport's entry in desktop/models.toml, with `folder` and `python` as Paths."""
    with open(DESKTOP / "models.toml", "rb") as f:
        entry = next(m for m in tomllib.load(f)["model"] if m["id"] == sport)
    folder = Path(entry["folder"])
    return {**entry, "folder": folder, "python": folder / entry["python"]}


# --- Time ---------------------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(value) -> datetime | None:
    """A kickoff from a CSV or a schedule as an aware UTC datetime, or None.
    Naive values are taken as UTC."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


def file_time(path: Path) -> datetime | None:
    """When a file was last written (UTC), or None if it doesn't exist."""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path.exists() else None


def written_since(path: Path, start: datetime) -> bool:
    t = file_time(path)
    return t is not None and t >= start - timedelta(seconds=2)


def ago(when: datetime | None, now: datetime) -> str:
    """'just now', '25 min ago', '5 h ago', '3 days ago' (or 'never')."""
    if when is None:
        return "never"
    s = (now - when).total_seconds()
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} min ago"
    if s < 2 * 86400:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} days ago"


def span(delta: timedelta) -> str:
    """'30 min', '12 h', '7 days'."""
    s = delta.total_seconds()
    if s < 3600:
        return f"{round(s / 60)} min"
    if s < 2 * 86400:
        return f"{round(s / 3600)} h"
    return f"{round(s / 86400)} days"


def et_day(when: datetime) -> str:
    """'Sat Oct 3' for a moment, in Eastern time."""
    d = when.astimezone(ET)
    return f"{d:%a %b} {d.day}"


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


# --- Plan -----------------------------------------------------------------------------------------

class History:
    """When each of a sport's steps last ran, from the app's state file."""

    def __init__(self, sport: str, path: Path = STATE_FILE):
        self.sport = sport
        try:
            self.steps = json.loads(path.read_text(encoding="utf-8")).get("steps", {})
        except (OSError, ValueError):
            self.steps = {}

    def last(self, step: str, what: str = "success") -> datetime | None:
        """`what` is 'success' or 'attempt'."""
        return to_utc(self.steps.get(f"{self.sport}.{step}", {}).get(what))


@dataclass
class Plan:
    """What a sport wants run now. `label` is what its model is on ("Week 5", "Sat Oct 3");
    `info` holds small stats the app shows (an API budget); `notes` are one-line heads-ups."""

    sport: str
    label: str = ""
    steps: list = field(default_factory=list)
    info: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def step(self, id: str, label: str, args: list, *, due: bool, reason: str,
             requires: tuple = ()) -> None:
        """A step the app runs as `<sport>.py step <args>`. `requires`: steps of this pass that
        must have succeeded first (a season sim after new projections)."""
        self.steps.append({"id": id, "label": label, "args": [str(a) for a in args],
                           "due": bool(due), "reason": reason, "requires": list(requires)})

    def stat(self, label: str, value: str, detail: str = "", tone: str | None = None) -> None:
        self.info.append({"label": label, "value": value, "detail": detail, "tone": tone})

    def emit(self) -> int:
        print("@@plan " + json.dumps(asdict(self)), flush=True)
        return 0


# --- Step -----------------------------------------------------------------------------------------

def run(cmd: list, cwd: Path, hide: tuple[str, ...] = ()) -> int:
    """Run a model command in its folder; its output streams straight through. `hide`: environment
    variables the model mustn't see (a key to an API whose calls cost money)."""
    cmd = [str(c) for c in cmd]
    print(f"$ {subprocess.list2cmdline(cmd)}", flush=True)
    env = {k: v for k, v in os.environ.items() if k not in hide}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if not sys.stdout.isatty():
        env.setdefault("COLUMNS", "140")  # the app's log, not a terminal: room for the tables
    proc = subprocess.Popen(cmd, cwd=cwd, env=env)
    for freeze in list(_held):  # a recovery must also wait for the model itself to finish
        freeze.journal(model_pid=proc.pid)
    return proc.wait()


def finish(ok: bool, summary: str, warnings: list | tuple = ()) -> int:
    """The step's outcome: a line for people, then one for the app."""
    print(f"\n{'OK' if ok else 'FAILED'}: {summary}", flush=True)
    for w in warnings:
        print(f"  warning: {w}", flush=True)
    print("@@result " + json.dumps({"ok": ok, "summary": summary, "warnings": list(warnings)}),
          flush=True)
    return 0 if ok else 1


def backup(sport: str, path: Path) -> Path:
    """Copy a model output into desktop/.state/backups/<sport>/ before a run rewrites it."""
    folder = BACKUPS / sport
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{path.name}"
    shutil.copyfile(path, dest)
    cutoff = time.time() - KEEP_BACKUPS_DAYS * 86400
    for old in folder.iterdir():
        if old.is_file() and old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    return dest


def read_rows(path: Path) -> pd.DataFrame:
    """A CSV as text, exactly as written (no NaN, no float reformatting)."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def write_rows(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False, lineterminator="\n")
    os.replace(tmp, path)


class Freeze:
    """The kickoff freeze for one slate file - a football week, a hockey day.

    A slate file is the record each model's grading scores, so what it says
    about a game has to be what the model said BEFORE that game started.
    Around a model run, `hold()` keeps a copy of the file (plus a backup under
    desktop/.state), and `settle()` rewrites the new file so that every game
    that kicked off before the run started keeps its earlier rows - or, with
    no earlier rows, loses the projection made after kickoff - and a game the
    run left out keeps its earlier rows. Kept rows are byte-identical text.

    `key` names the columns that identify a game; `kickoff(row)` gives its
    start (UTC) or None; `predicted(row)` says whether a row holds a
    projection (the NHL writes a game that has started as an empty row).
    `carry_unstarted=False` lets a run drop a game that hasn't started (a bet
    the model no longer flags) - only started games then keep missing rows.

    A held file is noted in desktop/.state/pending/ until it's settled, so if
    the step dies mid-run (killed, a crash) the next plan or step of that sport
    finishes the job from the backup, once the model process has exited too.
    """

    def __init__(self, sport: str, path: Path, key: list[str],
                 kickoff: Callable[[dict], datetime | None],
                 predicted: Callable[[dict], bool] = lambda row: True,
                 carry_unstarted: bool = True):
        self.sport, self.path, self.key = sport, path, key
        self.kickoff, self.predicted = kickoff, predicted
        self.carry_unstarted = carry_unstarted
        self.start = utcnow()
        self.before: pd.DataFrame | None = None
        self.saved: Path | None = None
        self.mtime: float | None = None
        self.note = PENDING / f"{sport}__{path.name}.json"

    def hold(self, start: datetime) -> None:
        self.start = start
        if self.path.exists():
            self.mtime = self.path.stat().st_mtime
            self.before = read_rows(self.path)
            self.saved = backup(self.sport, self.path)
        _held.append(self)
        self.journal()

    def journal(self, model_pid: int | None = None) -> None:
        PENDING.mkdir(parents=True, exist_ok=True)
        self.note.write_text(json.dumps({
            "path": str(self.path), "saved": str(self.saved) if self.saved else None,
            "mtime": self.mtime, "start": self.start.isoformat(), "step_pid": os.getpid(),
            "model_pid": model_pid}), encoding="utf-8")

    def release(self) -> None:
        """Settled or undone: nothing left to recover."""
        self.note.unlink(missing_ok=True)
        if self in _held:
            _held.remove(self)

    @property
    def rewritten(self) -> bool:
        return self.path.exists() and self.path.stat().st_mtime != self.mtime

    def undo(self) -> bool:
        """Put the held file back after a failed run. True if there was one."""
        self.release()
        if self.saved is None or not self.rewritten:
            return False
        shutil.copyfile(self.saved, self.path)
        os.utime(self.path, (time.time(), self.mtime))  # as it was: a failed run isn't a recent one
        return True

    def _started(self, rows: list[dict]) -> bool:
        for row in rows:
            k = self.kickoff(row)
            if k is not None and k <= self.start:
                return True
        return False

    def settle(self) -> dict:
        """Apply the freeze to the file the run wrote. Returns counts: `kept` games that had
        started (earlier rows put back), `dropped` projections made after kickoff with no
        earlier version, `carried` games the run left out, `unknown` games with no kickoff."""
        counts = self._settle()
        self.release()
        return counts

    def _settle(self) -> dict:
        counts = {"kept": 0, "dropped": 0, "carried": 0, "unknown": 0}
        if not self.path.exists() or (self.before is not None and not self.rewritten):
            return counts
        new = read_rows(self.path)
        old = _groups(self.before, self.key) if self.before is not None else {}
        fresh_groups = _groups(new, self.key)
        rows: list[dict] = []
        for k, group in fresh_groups.items():
            earlier = old.get(k)
            if all(self.kickoff(r) is None for r in group + (earlier or [])):
                counts["unknown"] += 1
            started = self._started(group) or self._started(earlier or [])
            fresh = any(self.predicted(r) for r in group)
            if started or not fresh:
                if earlier and any(self.predicted(r) for r in earlier):
                    if earlier != group:
                        counts["kept"] += 1
                    rows += earlier
                    continue
                if started and fresh:
                    counts["dropped"] += 1
                    continue
            rows += group
        for k, group in old.items():
            if k not in fresh_groups and (self.carry_unstarted or self._started(group)):
                counts["carried"] += 1
                rows += group
        if counts["kept"] or counts["dropped"] or counts["carried"]:
            columns = list(new.columns)
            if self.before is not None:
                columns += [c for c in self.before.columns if c not in columns]
            write_rows(pd.DataFrame(rows, columns=columns).fillna(""), self.path)
        return counts


_held: list[Freeze] = []  # this step's held files, so run() can note the model's pid on them


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes  # (os.kill on Windows would terminate the process)
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def recover(sport: str, freeze_for: Callable[[Path], Freeze | None], own: bool = False) -> list[str]:
    """Finish freezes that a step died before settling: once neither the step nor its model is
    still running, re-apply the freeze to the file from the backup. `own`: this process's notes
    (a step ending on an error). Returns what was done, one line per file."""
    done = []
    for note in sorted(PENDING.glob(f"{sport}__*.json")):
        try:
            info = json.loads(note.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            note.unlink(missing_ok=True)
            continue
        mine = info.get("step_pid") == os.getpid()
        if own != mine:
            continue
        old = utcnow() - (to_utc(info.get("start")) or utcnow()) > STALE_PENDING
        if not old and (process_alive(info.get("model_pid"))
                        or (not mine and process_alive(info.get("step_pid")))):
            continue  # still running: leave it for later
        freeze = freeze_for(Path(info["path"]))
        if freeze is not None and freeze.path.exists():
            freeze.start = to_utc(info["start"]) or utcnow()
            freeze.mtime = info.get("mtime")
            freeze.saved = Path(info["saved"]) if info.get("saved") else None
            freeze.before = read_rows(freeze.saved) if freeze.saved and freeze.saved.exists() else None
            parts, _ = freeze_notes(freeze._settle())
            if parts:  # (a run that died before its model wrote anything needs no word)
                done.append(f"Finished an interrupted run's kickoff freeze on {freeze.path.name}: "
                            + ", ".join(parts))
        note.unlink(missing_ok=True)
    return done


def _groups(df: pd.DataFrame, key: list[str]) -> dict[tuple, list[dict]]:
    """Rows by game, in file order."""
    out: dict[tuple, list[dict]] = {}
    for row in df.to_dict("records"):
        out.setdefault(tuple(row[k] for k in key), []).append(row)
    return out


def freeze_notes(counts: dict) -> tuple[list[str], list[str]]:
    """(summary parts, warnings) for a settle() result."""
    parts, warnings = [], []
    if counts["kept"]:
        parts.append(f"kept {plural(counts['kept'], 'game')} that had started")
    if counts["dropped"]:
        parts.append(f"left out {plural(counts['dropped'], 'game')} projected after kickoff")
    if counts["carried"]:
        warnings.append(f"{plural(counts['carried'], 'game')} missing from this run kept "
                        "their earlier projection")
    if counts["unknown"]:
        warnings.append(f"no kickoff time found for {plural(counts['unknown'], 'game')}, "
                        "so they couldn't be frozen")
    return parts, warnings


# --- Command line ---------------------------------------------------------------------------------

def cli(sport: str, plan: Callable[[History, bool], Plan],
        steps: dict[str, Callable[[argparse.Namespace], int]],
        freeze_for: Callable[[Path], Freeze | None]) -> int:
    """The module's command line. `freeze_for(path)` builds the Freeze for one of the sport's
    slate files - used by its steps and to finish an interrupted run's freeze."""
    ap = argparse.ArgumentParser(prog=f"{sport}.py", description=sys.modules["__main__"].__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="print what is due now (runs nothing)")
    p.add_argument("--state", type=Path, default=STATE_FILE, help="the app's run history")
    p.add_argument("--force", action="store_true", help="every step, however recently it ran")
    s = sub.add_parser("step", help="run one model step, with the kickoff freeze and checks")
    s.add_argument("name", choices=sorted(steps))
    s.add_argument("--season", type=int)
    s.add_argument("--week", type=int)
    s.add_argument("--date", help="a game day, YYYY-MM-DD")
    args = ap.parse_args()
    recovered = recover(sport, freeze_for)
    if args.command == "plan":
        p = plan(History(sport, args.state), args.force)
        p.notes = recovered + p.notes
        return p.emit()
    for line in recovered:
        print(line, flush=True)
    try:
        return steps[args.name](args)
    except Exception as error:  # report it the app's way rather than as a bare traceback
        traceback.print_exc()
        return finish(False, f"{args.name} crashed: {type(error).__name__}: {error}")
    finally:  # a step that ended on an error mid-freeze: settle what it held
        for line in recover(sport, freeze_for, own=True):
            print(line, flush=True)
