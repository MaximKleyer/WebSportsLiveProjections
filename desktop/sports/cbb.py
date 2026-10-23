"""College basketball for the desktop app: what to run when, and each step.

Runs with the CBB model's venv Python:
  python desktop/sports/cbb.py plan             # what's due now (runs nothing)
  python desktop/sports/cbb.py step today       # grade and predict: `cbb daily` for today
  python desktop/sports/cbb.py step early       # the early look at the coming game days (`cbb early`)
  python desktop/sports/cbb.py step rankings    # every D1 team by the model's rating (`cbb rankings`)
  python desktop/sports/cbb.py step season      # the season outlook and conference tournaments (`cbb season`)
  python desktop/sports/cbb.py step ncaa        # the NCAA tournament, once its bracket is out (`cbb ncaa`)

Each pass brings the day up to date, as the NHL's does. `cbb daily` for today refreshes the
schedule, results, Barttorvik's inbox, the availability file and lines, closes and settles
earlier bets, refits if due, predicts every game not yet tipped off (Barttorvik alone and with
the twist) and flags bets. The early look, the rankings and the season and NCAA sims run after
it, on the new results.

The model's "CBB gameday" scheduled task adds what needs a clock: it starts at 12:05 AM, so the
day's predictions are in before pre-dawn tips (Nov 2 has two), and reruns a later tip's games
40 minutes before it when an input changed. The model's run lock (core/data/lock.py) holds one
step at a time, so the task and the app never run at once. Until the model has that lock, the
task owns the day and the app runs nothing that writes, as it did before 2026-10-23.

The daily run keeps the rows of games already under way when it rewrites a day's file, so no
step needs the kickoff freeze. The early look (`cbb early`) predicts the coming game days for the
site on a read-only connection: nothing stored, no bets.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

from _common import (ET, Freeze, History, Plan, ago, cli, et_day, file_time, finish, model_entry, plural, run, span,
                     to_utc, utcnow, written_since)

MODEL = model_entry("cbb")
FOLDER = MODEL["folder"]
CBB = MODEL["python"].with_name("cbb.exe")
OUT = FOLDER / "outputs"
DAILY = OUT / "daily"
RANKINGS = OUT / "rankings.csv"
EARLY = OUT / "early"
DB = FOLDER / "data" / "cbb.sqlite"  # read-only here: what's on the schedule, what each run logged
LOCK = FOLDER / "core" / "data" / "lock.py"  # the model's run lock: without it, the gameday task owns the day
INBOX = FOLDER / "data" / "torvik" / "inbox"
AVAILABILITY = FOLDER / "config" / "availability.yaml"
TORVIK_FILES = ("*_team_results.csv", "trank_data.csv", "*_fffinal.csv")
TASK = "CBB gameday"

TODAY_EVERY = timedelta(hours=1)  # the availability file, Barttorvik's files and lines through the day
GAME_LENGTH = timedelta(hours=2, minutes=30)  # a game's result is in by then
REFRESH_EVERY = timedelta(hours=20)  # the rankings and the sims, at least: they also follow new results
EARLY_EVERY = timedelta(hours=6)  # cheap (seconds, no network): the latest Barttorvik file and lines through the day
HIDE = ("ODDS_API_KEY",)  # the model takes its lines from ESPN; nothing the app runs can spend credits
PLAYED = "status NOT IN ('postponed', 'canceled')"


def newest_sim(kind: str = "season") -> Path | None:
    """The newest `cbb season` file, or (kind "ncaa") `cbb ncaa` file."""
    sims = sorted((OUT / "sims").glob(f"{kind}_????_????-??-??.csv"), key=lambda p: p.name[-14:])
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
    except sqlite3.OperationalError:  # a model from before the table or column
        return []
    finally:
        con.close()


def todays_games(day: date) -> list[tuple[datetime, bool]]:
    """The date's D1 games: (tip-off, whether the time is announced). An unannounced one sits at a
    05:00 UTC placeholder and counts as later that day."""
    return [(to_utc(ts), bool(valid)) for ts, valid in
            read_db(f"SELECT start_ts, time_valid FROM games WHERE date = ? AND {PLAYED}", (day.isoformat(),))]


def ahead_of(games: list[tuple[datetime, bool]], now: datetime) -> list[tuple[datetime, bool]]:
    return [g for g in games if not g[1] or g[0] > now]


def finished_between(since: datetime, now: datetime) -> int:
    """Games with an announced tip whose results came in between the two moments (a game counts
    as over GAME_LENGTH after its tip)."""
    rows = read_db(f"SELECT COUNT(*) FROM games WHERE time_valid = 1 AND {PLAYED} AND start_ts > ? AND start_ts <= ?",
                   (db_ts(since - GAME_LENGTH), db_ts(now - GAME_LENGTH)))
    return rows[0][0] if rows else 0


def bracket_out() -> bool:
    """Whether the model has this season's NCAA bracket (the field ESPN's tournament games show)."""
    rows = read_db("SELECT COUNT(*) FROM ncaa_field WHERE season = (SELECT MAX(season) FROM d1_seasons)")
    return bool(rows and rows[0][0])


def latest_day() -> str | None:
    """The newest day the model logged (a rehearsal's <date>_dry_* files don't count)."""
    days = sorted(p.name[:10] for p in DAILY.glob("????-??-??_predictions.csv"))
    return days[-1] if days else None


def gameday_task() -> tuple[str, str]:
    """The scheduled task's state and next run, as Task Scheduler reports them."""
    try:
        out = subprocess.run(["schtasks", "/query", "/tn", TASK, "/fo", "csv", "/v"], capture_output=True,
                             text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        row = next(csv.DictReader(io.StringIO(out.stdout)))
    except (OSError, StopIteration, subprocess.SubprocessError):
        return "Not found", f'no scheduled task "{TASK}": no predictions before pre-dawn tips, no reruns before later ones'
    state = "Disabled" if row.get("Scheduled Task State") == "Disabled" else (row.get("Status") or "?")
    nxt = row.get("Next Run Time") or ""
    return state, "pre-dawn tips, reruns before later ones" + (f"; next run {nxt}" if nxt and nxt != "N/A" else "")


def need_inputs(p: Plan) -> None:
    """The checklist of what only the user can give the model (the app's "Your inputs"): the
    schedule, results and lines are all pulled by the daily run."""
    now = utcnow()
    local = now.astimezone(ET)
    games = len(todays_games(local.date()))
    stored = to_utc(max((ts for table in ("torvik_team_snapshots", "torvik_player_snapshots", "torvik_four_factors")
                         for (ts,) in read_db(f"SELECT MAX(snapshot_ts) FROM {table}") if ts), default=None))
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
           detail=f'The "{TASK}" task starts at 12:05 AM, predicts 75 minutes before the first tip and reruns '
                  "later tips' games; wake timers only work on AC power, and a sleeping laptop misses the tips "
                  "it sleeps through")


def plan(history: History, force: bool) -> Plan:
    now = utcnow()
    p = Plan("cbb")
    day = latest_day()
    p.label = et_day(to_utc(day + "T17:00:00")) if day else "No game day yet"

    today = now.astimezone(ET).date()
    games = todays_games(today)
    ahead = ahead_of(games, now)
    last = history.last("today")
    if not LOCK.exists():
        due, reason = False, f'the model has no run lock yet, so the "{TASK}" task owns the day'
    elif last is None or last < datetime.combine(today, time.min, ET):
        due = True
        reason = f"first run today: {plural(len(games), 'D1 game')}" if games else \
            "first run today: no games, so it only refreshes the schedule, Barttorvik's files and lines"
    elif finished := finished_between(last, now):
        due, reason = True, f"{plural(finished, 'game')} finished since it last ran"
    elif ahead and now - last >= TODAY_EVERY:
        due, reason = True, f"last run {ago(last, now)}"
    else:
        due = force
        reason = f"ran {ago(last, now)}" + (f" (refreshes every {span(TODAY_EVERY)} while games are ahead)" if ahead
                                            else "; every game today has tipped off" if games else "; no games today")
    p.step("today", "Grade and predict today", ["today"], due=due, reason=reason)
    after = ("today",) if due else ()

    written = max((file_time(f) for f in EARLY.glob("????-??-??.csv")), default=None)
    stale = written is None or now - written >= EARLY_EVERY
    p.step("early", "Early look", ["early"], due=force or stale, requires=after,
           reason="never run" if written is None else
           f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(EARLY_EVERY)})"))

    # After the day's run, on its results: whenever a game has finished since they last ran.
    sim, ncaa = newest_sim(), newest_sim("ncaa")
    sims = [("rankings", "Rankings", file_time(RANKINGS)), ("season", "Season sim", file_time(sim) if sim else None)]
    if bracket_out():
        sims.append(("ncaa", "NCAA tournament", file_time(ncaa) if ncaa else None))
    for step, label, written in sims:
        stale = written is None or now - written >= REFRESH_EVERY
        new = 0 if written is None else finished_between(written, now)
        p.step(step, label, [step], due=force or stale or new > 0, requires=after,
               reason=("the bracket is out" if step == "ncaa" else "never run") if written is None else
               f"{plural(new, 'game')} finished since it ran" if new and not stale else
               f"last run {ago(written, now)}" + ("" if stale else f" (refreshes every {span(REFRESH_EVERY)}, or after results)"))
    if not bracket_out():
        p.step("ncaa", "NCAA tournament", ["ncaa"], due=False, reason="no bracket until Selection Sunday")

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
    text = f"graded {', '.join(et_day(to_utc(d + 'T17:00:00')) for d in days)}: {plural(len(finals), 'result')}"
    if settled:
        record = {r: sum(1 for s in settled if s[0] == r) for r in ("win", "loss", "push")}
        units = sum(profit(*s[:3]) for s in settled)
        text += (f", bets {record['win']}-{record['loss']}" + (f"-{record['push']}" if record["push"] else "")
                 + f" ({units:+.2f} u)")
    return text


def step_today(args: argparse.Namespace) -> int:
    if not LOCK.exists():
        return finish(True, f'Skipped: the CBB model has no run lock yet, so the "{TASK}" task owns the day')
    start = utcnow()
    today = start.astimezone(ET).date()
    ahead = ahead_of(todays_games(today), start)
    open_bets = {b for (b,) in read_db("SELECT bet_id FROM bets WHERE result IS NULL")}
    unscored = {g for (g,) in read_db(f"SELECT game_id FROM games WHERE home_pts IS NULL AND time_valid = 1 AND {PLAYED}"
                                      " AND start_ts < ?", (db_ts(start),))}
    code = run([CBB, "daily", "--date", today.isoformat()], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"cbb daily exited with code {code}")

    parts, warnings = [], []
    label = et_day(datetime.combine(today, time(12), ET))
    since = db_ts(start - timedelta(seconds=2))
    if ahead:
        rows = read_db("SELECT COUNT(DISTINCT p.game_id) FROM predictions p JOIN games g USING (game_id) "
                       "WHERE g.date = ? AND p.created_ts >= ?", (today.isoformat(), since))
        made = rows[0][0] if rows else 0
        rows = read_db("SELECT SUM(p.created_ts >= ?), COUNT(*) FROM bets b JOIN predictions p USING (pred_id) "
                       "JOIN games g USING (game_id) WHERE g.date = ?", (since, today.isoformat()))
        new, total = (rows[0][0] or 0, rows[0][1]) if rows else (0, 0)
        games = plural(made, "game") if made == len(ahead) else f"{made} of {len(ahead)} games"
        parts.append(f"{label}: {games} predicted, {plural(new, 'new bet')}"
                     + (f" ({total} on the day)" if total and total != new else ""))
        if made == 0:
            warnings.append("no game got a prediction")
    else:
        parts.append(f"{label}: no game left to predict")
    parts.append(graded(open_bets, unscored) or "nothing new to grade")
    return finish(True, "; ".join(parts), warnings)


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


def step_early(args: argparse.Namespace) -> int:
    start = utcnow()
    code = run([CBB, "early"], FOLDER, hide=HIDE)
    if code != 0:
        return finish(False, f"cbb early exited with code {code}")
    written = [f for f in EARLY.glob("????-??-??.csv") if written_since(f, start)]
    return finish(True, f"Early look updated ({len(written)} game days)" if written else "No games ahead to look at")


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
    return None  # the daily run keeps the rows of games already under way (see above)


if __name__ == "__main__":
    raise SystemExit(cli("cbb", plan, {"today": step_today, "early": step_early, "rankings": step_rankings,
                                       "season": step_season, "ncaa": step_ncaa}, freeze_for))
