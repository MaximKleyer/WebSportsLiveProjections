"""NBA for the desktop app: what to run when, and each step.

Runs with the NBA model's venv Python:
  python desktop/sports/nba.py plan            # what's due now (runs nothing)
  python desktop/sports/nba.py step today      # grade and predict: `nba daily` for today
  python desktop/sports/nba.py step early      # the early look at the coming week (scripts/early.py)
  python desktop/sports/nba.py step season     # the season simulation (`nba preseason`)
  python desktop/sports/nba.py step power      # power rankings

Each pass brings the day up to date, as the NHL's does. `nba daily` for today pulls in the
results, settles the bets of finished games, predicts every game not yet started and flags bets;
once every game has tipped off, it only pulls and settles. The early look, the season sim and
the power rankings run after it, on the new results.

The model's "NBA gameday" scheduled task adds what needs a clock: a rerun 40 minutes before each
tip with the newest injury report, and closing lines just before tip-off. The model's run lock
(core/data/lock.py) holds one step at a time, so the task and the app never run at once. Until
the model has that lock, the task owns the day and the app runs nothing that writes, as it did
before 2026-10-23.

The model's daily run keeps the rows of games already under way when it rewrites a day's file,
so no step needs the kickoff freeze. The early look predicts the coming week's games for the
site with the daily run's own code in its no-store mode, on a read-only connection: nothing
stored, no bets.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from _common import (ET, Freeze, History, Plan, ago, cli, et_day, file_time, finish, model_entry, plural, run, span,
                     to_utc, utcnow, written_since)

MODEL = model_entry("nba")
FOLDER = MODEL["folder"]
NBA = MODEL["python"].with_name("nba.exe")
DB = FOLDER / "data" / "nba.sqlite"  # read-only here: what's on the schedule, what each run logged
LOCK = FOLDER / "core" / "data" / "lock.py"  # the model's run lock: without it, the gameday task owns the day
OUT = FOLDER / "outputs"
DAILY = OUT / "daily"
POWER = OUT / "power_rankings.csv"
EARLY = OUT / "early"
EARLY_SCRIPT = FOLDER / "scripts" / "early.py"  # outside the model's code paths: model_version stays put
TASK = "NBA gameday"

TODAY_EVERY = timedelta(hours=1)  # fresh injury reports and lines through the day
GAME_LENGTH = timedelta(hours=2, minutes=30)  # a game's result is in by then
SIM_EVERY = timedelta(hours=20)
EARLY_EVERY = timedelta(hours=6)  # cheap (about 30 s, no network): fresh injury reports and lines through the day
HIDE = ("ODDS_API_KEY",)  # lines come from ESPN (sources.odds_source): nothing the app runs can spend credits


def newest_sim() -> Path | None:
    sims = sorted((OUT / "sims").glob("season_????_????-??-??.csv"), key=lambda p: p.name[-14:])
    return sims[-1] if sims else None


def db_ts(when: datetime) -> str:
    """A moment as the model stores it (UTC text, so string order is time order)."""
    return f"{to_utc(when):%Y-%m-%dT%H:%M:%SZ}"


def read_db(sql: str, params: tuple | list = ()) -> list[tuple]:
    if not DB.exists():
        return []
    con = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def tip_offs(day: date) -> list[datetime]:
    return [to_utc(ts) for (ts,) in read_db("SELECT start_ts FROM games WHERE date = ?", (day.isoformat(),))]


def finished_between(since: datetime, now: datetime) -> int:
    """Games whose results came in between the two moments (a game counts as over GAME_LENGTH after its tip)."""
    [(n,)] = read_db("SELECT COUNT(*) FROM games WHERE start_ts > ? AND start_ts <= ?",
                     (db_ts(since - GAME_LENGTH), db_ts(now - GAME_LENGTH))) or [(0,)]
    return n


def live_version() -> str | None:
    """The model version outputs/model_state.json was fitted for: the one the exporter publishes
    early looks of."""
    try:
        return json.loads((OUT / "model_state.json").read_text(encoding="utf-8"))["model_version"]
    except (OSError, ValueError, KeyError):
        return None


def early_version() -> str | None:
    """The model version the latest early look was made with."""
    files = sorted(EARLY.glob("????-??-??.csv"), key=lambda f: f.stat().st_mtime)
    try:
        versions = pd.read_csv(files[-1], usecols=["model_version"])["model_version"] if files else []
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    return str(versions.iloc[-1]) if len(versions) else None


def latest_day() -> str | None:
    """The newest day the exporter publishes: one whose day file has a game's prediction as the model
    logged it live - its latest stored on game day before tip-off, by whichever version made it
    (`logged_live` in scripts/exporters/nba.py). A rehearsal's leftover day files, predicted days
    ahead, don't count."""
    files = {p.name[:10]: p for p in DAILY.glob("????-??-??_predictions.csv")}
    if not files or not DB.exists():
        return None
    rows = read_db(
        "SELECT p.game_id, g.date, p.created_ts, p.model_version FROM games g JOIN predictions p USING (game_id) "
        f"WHERE g.date IN ({', '.join('?' * len(files))}) AND p.created_ts < g.start_ts "
        "ORDER BY p.created_ts, p.pred_id", list(files))
    # Oldest first, so each game's latest prediction on its day is the one left.
    live = {game: (day, version) for game, day, created, version in rows
            if to_utc(created).astimezone(ET).date().isoformat() == day}
    for day in sorted(files, reverse=True):
        try:
            df = pd.read_csv(files[day], usecols=["game_id", "model_version"], dtype={"game_id": str})
        except (OSError, ValueError, pd.errors.ParserError):
            continue  # being rewritten this moment
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
        return "Not found", f'no scheduled task "{TASK}": no reruns before tip-off or closing lines'
    state = "Disabled" if row.get("Scheduled Task State") == "Disabled" else (row.get("Status") or "?")
    nxt = row.get("Next Run Time") or ""
    return state, "reruns before each tip, closing lines" + (f"; next run {nxt}" if nxt and nxt != "N/A" else "")


def need_inputs(p: Plan) -> None:
    """The checklist of what only the user can do for the model (the app's "Your inputs"): injury
    reports, rosters and odds are all pulled by the daily run."""
    live = live_version()
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
           detail=f'The "{TASK}" task reruns each game 40 minutes before its tip and pulls closing lines just '
                  "before it, but only while the PC is awake; wake timers only work on AC power")


def plan(history: History, force: bool) -> Plan:
    now = utcnow()
    p = Plan("nba")
    day = latest_day()
    p.label = et_day(to_utc(day + "T12:00:00")) if day else "No game day yet"

    today = now.astimezone(ET).date()
    tips = tip_offs(today)
    ahead = [t for t in tips if t > now]
    last = history.last("today")
    if not LOCK.exists():
        due, reason = False, f'the model has no run lock yet, so the "{TASK}" task owns the day'
    elif last is None or last < datetime.combine(today, time.min, ET):
        due = True
        reason = (f"first run today: {plural(len(tips), 'game')}, the first at {min(tips).astimezone(ET):%H:%M}"
                  if tips else "first run today: no games, so it brings in results and settles bets")
    elif finished := finished_between(last, now):
        due, reason = True, f"{plural(finished, 'game')} finished since it last ran"
    elif ahead and now - last >= TODAY_EVERY:
        due, reason = True, f"last run {ago(last, now)}"
    else:
        due = force
        reason = f"ran {ago(last, now)}" + (f" (refreshes every {span(TODAY_EVERY)} while games are ahead)" if ahead
                                            else "; every game today has tipped off")
    p.step("today", "Grade and predict today", ["today"], due=due, reason=reason)
    after = ("today",) if due else ()

    written = max((file_time(f) for f in EARLY.glob("????-??-??.csv")), default=None)
    made_by, live = early_version(), live_version()
    changed = made_by is not None and live is not None and made_by != live  # the site shows only the live one's
    stale = written is None or now - written >= EARLY_EVERY or changed
    p.step("early", "Early look", ["early"], due=force or stale, requires=after,
           reason="never run" if written is None else
           f"made by {made_by}, and the model is now {live}" if changed else
           f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(EARLY_EVERY)})"))

    # After the day's run, on its results: whenever a game has finished since they last ran.
    sim = newest_sim()
    for step, label, written in (("season", "Season sim", file_time(sim) if sim else None),
                                 ("power", "Power rankings", file_time(POWER))):
        stale = written is None or now - written >= SIM_EVERY
        new = 0 if written is None else finished_between(written, now)
        p.step(step, label, [step], due=force or stale or new > 0, requires=after,
               reason="never run" if written is None else
               f"{plural(new, 'game')} finished since it ran" if new and not stale else
               f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(SIM_EVERY)}, or after results)"))

    state, detail = gameday_task()
    p.stat("Gameday task", state, detail, tone=None if state in ("Ready", "Running") else "bad")
    need_inputs(p)
    return p


def profit(result: str, stake: float, price: float) -> float:
    if result == "win":
        return stake * (price / 100 if price > 0 else 100 / -price)
    return -stake if result == "loss" else 0.0


def graded(bets: set[int], games: set[str]) -> str | None:
    """What this run graded: the final scores that came in (of `games`, unscored before it) and
    the bets it settled (of `bets`, open before it), by game day."""
    if not bets and not games:
        return None
    marks = lambda n: ", ".join("?" * n)  # noqa: E731
    finals = read_db(f"SELECT date FROM games WHERE game_id IN ({marks(len(games))}) AND home_pts IS NOT NULL",
                     sorted(games)) if games else []
    settled = read_db(
        "SELECT b.result, b.stake, b.price, g.date FROM bets b JOIN predictions p USING (pred_id) "
        f"JOIN games g USING (game_id) WHERE b.bet_id IN ({marks(len(bets))}) AND b.result IS NOT NULL",
        sorted(bets)) if bets else []
    if not finals and not settled:
        return None
    days = sorted({d for (d,) in finals} | {d for *_, d in settled})
    text = f"graded {', '.join(et_day(to_utc(d + 'T12:00:00')) for d in days)}: {plural(len(finals), 'result')}"
    if settled:
        record = {r: sum(1 for s in settled if s[0] == r) for r in ("win", "loss", "push")}
        units = sum(profit(*s[:3]) for s in settled)
        text += (f", bets {record['win']}-{record['loss']}" + (f"-{record['push']}" if record["push"] else "")
                 + f" ({units:+.2f} u)")
    return text


def step_today(args: argparse.Namespace) -> int:
    if not LOCK.exists():
        return finish(True, f'Skipped: the NBA model has no run lock yet, so the "{TASK}" task owns the day')
    start = utcnow()
    today = start.astimezone(ET).date()
    ahead = [t for t in tip_offs(today) if t > start]
    open_bets = {b for (b,) in read_db("SELECT bet_id FROM bets WHERE result IS NULL")}
    unscored = {g for (g,) in read_db("SELECT game_id FROM games WHERE home_pts IS NULL AND start_ts < ?", (db_ts(start),))}
    cmd = [NBA, "daily", "--date", today.isoformat()]
    if not ahead:
        cmd += ["--no-predict", "--no-odds"]  # every game has tipped off (or there are none): results and bets only
    code = run(cmd, FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"nba daily exited with code {code}")

    parts, warnings = [], []
    label = et_day(datetime.combine(today, time(12), ET))
    if ahead:
        [(made,)] = read_db("SELECT COUNT(DISTINCT p.game_id) FROM predictions p JOIN games g USING (game_id) "
                            "WHERE g.date = ? AND p.created_ts >= ?", (today.isoformat(), db_ts(start - timedelta(seconds=2))))
        [(new, total)] = read_db(
            "SELECT SUM(p.created_ts >= ?), COUNT(*) FROM bets b JOIN predictions p USING (pred_id) "
            "JOIN games g USING (game_id) WHERE g.date = ?", (db_ts(start - timedelta(seconds=2)), today.isoformat()))
        games = plural(made, "game") if made == len(ahead) else f"{made} of {len(ahead)} games"
        parts.append(f"{label}: {games} predicted, {plural(new or 0, 'new bet')}"
                     + (f" ({total} on the day)" if total and total != (new or 0) else ""))
        if made == 0:
            warnings.append("no game got a prediction")
    else:
        parts.append(f"{label}: no game left to predict")
    parts.append(graded(open_bets, unscored) or "nothing new to grade")
    return finish(True, "; ".join(parts), warnings)


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
    return None  # the model's daily run keeps the rows of games already under way (see above)


if __name__ == "__main__":
    raise SystemExit(cli("nba", plan, {"today": step_today, "early": step_early, "season": step_season,
                                       "power": step_power}, freeze_for))
