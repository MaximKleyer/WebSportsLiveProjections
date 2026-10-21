"""NBA for the desktop app: what to run when - everything but the day's predictions.

Runs with the NBA model's venv Python:
  python desktop/sports/nba.py plan            # what's due now (runs nothing)
  python desktop/sports/nba.py step season     # the season simulation (`nba preseason`)
  python desktop/sports/nba.py step power      # power rankings
  python desktop/sports/nba.py step early      # the early look at the coming week (scripts/early.py)

The day's predictions belong to the model's own "NBA gameday" scheduled task: it runs from the
morning of each game day until the last tip-off, predicting on the model's clock (as soon as it
starts at 9 am, again 40 minutes before each tip, closing lines just before tip-off).
The model has no lock against a second run, so the app never runs `nba daily` or `nba gameday`;
it exports what the task wrote and keeps the season simulation and power rankings fresh. The
early look predicts the coming week's games for the site with the daily run's own code in its
no-store mode, on a read-only connection: nothing stored, no bets, and the site shows a day's
only until the gameday task's run for it. None of them rewrites a day's predictions, so no step
needs the kickoff freeze.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import subprocess
from datetime import timedelta
from pathlib import Path

import pandas as pd

from _common import (ET, Freeze, History, Plan, ago, cli, et_day, file_time, finish, model_entry, run, span, to_utc,
                     utcnow, written_since)

MODEL = model_entry("nba")
FOLDER = MODEL["folder"]
NBA = MODEL["python"].with_name("nba.exe")
DB = FOLDER / "data" / "nba.sqlite"  # read-only: which predictions were logged live
OUT = FOLDER / "outputs"
DAILY = OUT / "daily"
POWER = OUT / "power_rankings.csv"
EARLY = OUT / "early"
EARLY_SCRIPT = FOLDER / "scripts" / "early.py"  # outside the model's code paths: model_version stays put
TASK = "NBA gameday"

SIM_EVERY = timedelta(hours=20)
EARLY_EVERY = timedelta(hours=6)  # cheap (about 30 s, no network): fresh injury reports and lines through the day
HIDE = ("ODDS_API_KEY",)  # neither step uses odds: nothing the app runs can spend credits


def newest_sim() -> Path | None:
    sims = sorted((OUT / "sims").glob("season_????_????-??-??.csv"), key=lambda p: p.name[-14:])
    return sims[-1] if sims else None


def latest_day() -> str | None:
    """The newest day the exporter publishes: one whose day file has a game's prediction as the model
    logged it live - its latest stored on game day before tip-off, by whichever version made it
    (`logged_live` in scripts/exporters/nba.py). A rehearsal's leftover day files, predicted days
    ahead, don't count."""
    files = {p.name[:10]: p for p in DAILY.glob("????-??-??_predictions.csv")}
    if not files or not DB.exists():
        return None
    con = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            "SELECT p.game_id, g.date, p.created_ts, p.model_version FROM games g JOIN predictions p USING (game_id) "
            f"WHERE g.date IN ({', '.join('?' * len(files))}) AND p.created_ts < g.start_ts "
            "ORDER BY p.created_ts, p.pred_id", list(files)).fetchall()
    finally:
        con.close()
    # Oldest first, so each game's latest prediction on its day is the one left.
    live = {game: (day, version) for game, day, created, version in rows
            if to_utc(created).astimezone(ET).date().isoformat() == day}
    for day in sorted(files, reverse=True):
        try:
            df = pd.read_csv(files[day], usecols=["game_id", "model_version"], dtype={"game_id": str})
        except (OSError, ValueError, pd.errors.ParserError):
            continue  # being rewritten by the gameday task this moment
        if any(live.get(g) == (day, v) for g, v in zip(df["game_id"], df["model_version"])):
            return day
    return None


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


def need_inputs(p: Plan) -> None:
    """The checklist of what only the user can do for the model (the app's "Your inputs"): injury
    reports, rosters and odds are all pulled by the gameday task."""
    try:
        live = json.loads((OUT / "model_state.json").read_text(encoding="utf-8"))["model_version"]
    except (OSError, ValueError, KeyError):
        live = None
    try:
        out = subprocess.run([str(NBA), "version"], cwd=FOLDER, capture_output=True, text=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = out.stdout.strip().splitlines()
        code = lines[-1].strip() if out.returncode == 0 and lines else None
    except (OSError, subprocess.SubprocessError):
        code = None
    stale = code is not None and live is not None and (code != live or "dirty" in code)
    p.need("backtest", "Backtest after a model change", "after any code or config change",
           detail=(f"The model state is for {live}, the code is {code}. " if stale else "")
           + "Commit first (nba version must not say +dirty), then the backtest (~20 min), the preseason sim and "
             "the launch check (RUNBOOK)",
           how="uv run nba backtest; uv run nba preseason; uv run nba launch-check --date <next game day>",
           last=file_time(OUT / "model_state.json"), due=None if code is None or live is None else stale)
    p.need("awake", "Game days: plugged in, lid open", "each game day",
           detail=f'The "{TASK}" task wakes the PC at 9 AM to predict the day; wake timers only work on AC power')


def plan(history: History, force: bool) -> Plan:
    now = utcnow()
    p = Plan("nba")
    day = latest_day()
    p.label = et_day(to_utc(day + "T12:00:00")) if day else "No game day yet"

    sim = newest_sim()
    for step, label, written in (("season", "Season sim", file_time(sim) if sim else None),
                                 ("power", "Power rankings", file_time(POWER))):
        stale = written is None or now - written >= SIM_EVERY
        p.step(step, label, [step], due=force or stale,
               reason="never run" if written is None else
               f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(SIM_EVERY)})"))

    written = max((file_time(f) for f in EARLY.glob("????-??-??.csv")), default=None)
    stale = written is None or now - written >= EARLY_EVERY
    p.step("early", "Early look", ["early"], due=force or stale,
           reason="never run" if written is None else
           f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(EARLY_EVERY)})"))

    state, detail = gameday_task()
    p.stat("Gameday task", state, detail, tone=None if state in ("Ready", "Running") else "bad")
    need_inputs(p)
    return p


def step_season(args: argparse.Namespace) -> int:
    start = utcnow()
    code = run([NBA, "preseason"], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"nba preseason exited with code {code}")
    sim = newest_sim()
    if sim is None or not written_since(sim, start):
        return finish(False, "nba preseason didn't write a season simulation")
    return finish(True, f"Season sim updated ({sim.name})")


def step_power(args: argparse.Namespace) -> int:
    start = utcnow()
    code = run([NBA, "power"], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"nba power exited with code {code}")
    if not written_since(POWER, start):
        return finish(False, f"nba power didn't write {POWER.name}")
    return finish(True, "Power rankings updated")


def step_early(args: argparse.Namespace) -> int:
    start = utcnow()
    code = run([MODEL["python"], EARLY_SCRIPT], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"the early look exited with code {code}")
    written = [f for f in EARLY.glob("????-??-??.csv") if written_since(f, start)]
    return finish(True, f"Early look updated ({len(written)} game days)" if written else "No games ahead to look at")


def freeze_for(path: Path) -> Freeze | None:
    return None  # the app never rewrites a day's predictions (see above)


if __name__ == "__main__":
    raise SystemExit(cli("nba", plan, {"season": step_season, "power": step_power, "early": step_early}, freeze_for))
