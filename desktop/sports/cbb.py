"""College basketball for the desktop app: what to run when - everything but the day's predictions.

Runs with the CBB model's venv Python:
  python desktop/sports/cbb.py plan             # what's due now (runs nothing)
  python desktop/sports/cbb.py step rankings    # every D1 team by the model's rating (`cbb rankings`)
  python desktop/sports/cbb.py step season      # the season outlook and conference tournaments (`cbb season`)
  python desktop/sports/cbb.py step ncaa        # the NCAA tournament, once its bracket is out (`cbb ncaa`)

The day's predictions belong to the model's own "CBB gameday" scheduled task: it starts each day at
12:05 AM and runs the daily pipeline 75 minutes before the first tip (refresh, close and settle,
predict both ways, bets, outputs/daily/), then rechecks the games of each later tip. The model has
no lock against a second run, so the app never runs `cbb daily` or `cbb gameday`; it exports what
the task wrote and keeps the rankings, the season outlook and (from Selection Sunday) the NCAA
tournament's odds fresh. None rewrites a day's predictions, so no step needs the kickoff freeze.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import subprocess
from datetime import timedelta
from pathlib import Path

from _common import (ET, Freeze, History, Plan, ago, cli, et_day, file_time, finish, model_entry, run, span, to_utc,
                     utcnow, written_since)

MODEL = model_entry("cbb")
FOLDER = MODEL["folder"]
CBB = MODEL["python"].with_name("cbb.exe")
OUT = FOLDER / "outputs"
DAILY = OUT / "daily"
RANKINGS = OUT / "rankings.csv"
DB = FOLDER / "data" / "cbb.sqlite"
INBOX = FOLDER / "data" / "torvik" / "inbox"
AVAILABILITY = FOLDER / "config" / "availability.yaml"
TORVIK_FILES = ("*_team_results.csv", "trank_data.csv", "*_fffinal.csv")
TASK = "CBB gameday"

REFRESH_EVERY = timedelta(hours=20)  # the rankings and the sims: about daily, after the morning's results
HIDE = ("ODDS_API_KEY",)  # the model takes its lines from ESPN; nothing the app runs can spend credits


def newest_sim(kind: str = "season") -> Path | None:
    """The newest `cbb season` file, or (kind "ncaa") `cbb ncaa` file."""
    sims = sorted((OUT / "sims").glob(f"{kind}_????_????-??-??.csv"), key=lambda p: p.name[-14:])
    return sims[-1] if sims else None


def bracket_out() -> bool:
    """Whether the model has this season's NCAA bracket (the field ESPN's tournament games show)."""
    if not DB.exists():
        return False
    con = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        (field,) = con.execute("SELECT COUNT(*) FROM ncaa_field WHERE season = (SELECT MAX(season) FROM d1_seasons)").fetchone()
    except sqlite3.OperationalError:  # a model from before phase 11
        field = 0
    finally:
        con.close()
    return field > 0


def latest_day() -> str | None:
    """The newest day the gameday task logged (a rehearsal's <date>_dry_* files don't count)."""
    days = sorted(p.name[:10] for p in DAILY.glob("????-??-??_predictions.csv"))
    return days[-1] if days else None


def gameday_task() -> tuple[str, str]:
    """The scheduled task's state and next run, as Task Scheduler reports them."""
    try:
        out = subprocess.run(["schtasks", "/query", "/tn", TASK, "/fo", "csv", "/v"], capture_output=True,
                             text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        row = next(csv.DictReader(io.StringIO(out.stdout)))
    except (OSError, StopIteration, subprocess.SubprocessError):
        return "Not found", f'no scheduled task "{TASK}": the day\'s predictions won\'t run'
    state = "Disabled" if row.get("Scheduled Task State") == "Disabled" else (row.get("Status") or "?")
    nxt = row.get("Next Run Time") or ""
    return state, "predicts each game day" + (f"; next run {nxt}" if nxt and nxt != "N/A" else "")


def db_facts(today: str) -> tuple[int, str | None]:
    """From the model's database (read-only): today's D1 games, and when the latest Barttorvik file
    it has read was saved."""
    if not DB.exists():
        return 0, None
    con = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        (games,) = con.execute("SELECT COUNT(*) FROM games WHERE date = ? AND status NOT IN ('postponed', 'canceled')",
                               (today,)).fetchone()
        saved = max((ts for table in ("torvik_team_snapshots", "torvik_player_snapshots", "torvik_four_factors")
                     if (ts := con.execute(f"SELECT MAX(snapshot_ts) FROM {table}").fetchone()[0])), default=None)
    finally:
        con.close()
    return int(games), saved


def need_inputs(p: Plan) -> None:
    """The checklist of what only the user can give the model (the app's "Your inputs"): the
    schedule, results and lines are all pulled by the gameday task."""
    now = utcnow()
    local = now.astimezone(ET)
    games, saved = db_facts(local.date().isoformat())
    stored = to_utc(saved)
    files = [f for pattern in TORVIK_FILES for f in INBOX.glob(pattern)]  # the files the model reads (it leaves them there)
    newest = max((file_time(f) for f in files), default=None)
    waiting = [f for f in files if stored is None or file_time(f) > stored + timedelta(seconds=1)]
    last = max((t for t in (stored, newest) if t is not None), default=None)
    morning = local.replace(hour=5, minute=0, second=0, microsecond=0)
    p.need("torvik", "Barttorvik's files into the inbox", "each game-day morning, before the first tip",
           detail=(f"{games} D1 games today. " if games else "") + "Save the team results and player files from barttorvik.com"
                  + (f"; {len(waiting)} saved since the last run, read by the next one" if waiting else ""),
           how=f"{INBOX}: 2027_team_results.csv (T-Rank, CSV) and trank_data.csv (player stats, CSV)",
           last=last, due=bool(games) and (last is None or last < morning), again=None)
    p.need("availability", "Who's out: config/availability.yaml", "game days, as news comes in",
           detail="date -> team -> player: out / doubtful / questionable (or gtd) / probable / available. "
                  "A rotation player who missed his team's last game is already presumed out",
           how=str(AVAILABILITY), last=file_time(AVAILABILITY), due=None)
    p.need("awake", "Game days: plugged in, lid open", "each game day",
           detail=f'The "{TASK}" task starts at 12:05 AM and predicts 75 minutes before the first tip; wake timers '
                  "only work on AC power, and a sleeping laptop misses the tips it sleeps through")


def plan(history: History, force: bool) -> Plan:
    now = utcnow()
    p = Plan("cbb")
    day = latest_day()
    p.label = et_day(to_utc(day + "T17:00:00")) if day else "No game day yet"
    sim, ncaa = newest_sim(), newest_sim("ncaa")
    for step, label, written in (("rankings", "Rankings", file_time(RANKINGS)),
                                 ("season", "Season sim", file_time(sim) if sim else None)):
        stale = written is None or now - written >= REFRESH_EVERY
        p.step(step, label, [step], due=force or stale,
               reason="never run" if written is None else
               f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(REFRESH_EVERY)})"))
    if bracket_out():
        written = file_time(ncaa) if ncaa else None
        stale = written is None or now - written >= REFRESH_EVERY
        p.step("ncaa", "NCAA tournament", ["ncaa"], due=force or stale,
               reason="the bracket is out" if written is None else
               f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(REFRESH_EVERY)})"))
    else:
        p.step("ncaa", "NCAA tournament", ["ncaa"], due=False, reason="no bracket until Selection Sunday")
    state, detail = gameday_task()
    p.stat("Gameday task", state, detail, tone=None if state in ("Ready", "Running") else "bad")
    need_inputs(p)
    return p


def step_rankings(args: argparse.Namespace) -> int:
    start = utcnow()
    code = run([CBB, "rankings"], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"cbb rankings exited with code {code}")
    if not written_since(RANKINGS, start):
        return finish(False, f"cbb rankings didn't write {RANKINGS.name}")
    return finish(True, "Rankings updated")


def step_season(args: argparse.Namespace) -> int:
    start = utcnow()
    code = run([CBB, "season"], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"cbb season exited with code {code}")
    sim = newest_sim()
    if sim is None or not written_since(sim, start):
        return finish(False, "cbb season didn't write a season simulation")
    return finish(True, f"Season sim updated ({sim.name})")


def step_ncaa(args: argparse.Namespace) -> int:
    start = utcnow()
    code = run([CBB, "ncaa"], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"cbb ncaa exited with code {code}")
    sim = newest_sim("ncaa")
    if sim is None or not written_since(sim, start):
        return finish(True, "No NCAA bracket yet: it comes out on Selection Sunday")
    return finish(True, f"NCAA tournament sim updated ({sim.name})")


def freeze_for(path: Path) -> Freeze | None:
    return None  # the app never rewrites a day's predictions (see above)


if __name__ == "__main__":
    raise SystemExit(cli("cbb", plan, {"rankings": step_rankings, "season": step_season, "ncaa": step_ncaa}, freeze_for))
