"""College football for the desktop app: what to run when, each step run with the kickoff freeze.

Runs with the CFB model's venv Python:
  python desktop/sports/cfb.py plan              # what's due now (runs nothing)
  python desktop/sports/cfb.py step project --week 6
  python desktop/sports/cfb.py step season
  python desktop/sports/cfb.py step grade

CollegeFootballData allows 1,000 API calls a month on the free tier, and the
model caches the current season for 12 hours, so a projection with a stale
cache costs ~14 calls and grading always costs 2. The schedule (weeks,
kickoffs) is read from that cache - reading it costs nothing.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from _common import (Freeze, History, Plan, ago, cli, file_time, finish, freeze_notes, last_weekday, model_entry,
                     plural, read_rows, run, span, to_utc, utcnow, written_since)

MODEL = model_entry("cfb")
FOLDER, PY = MODEL["folder"], MODEL["python"]
OUT = FOLDER / "output"
CACHE = FOLDER / "data" / "cache"
CONFIG = FOLDER / "config.yaml"
COUNTER = CACHE / "_call_counter.json"
RESULTS = OUT / "results_log.csv"

GAME_LENGTH = timedelta(hours=4, minutes=30)
PROJECT_EVERY = timedelta(hours=36)    # each run with a stale cache costs ~14 of the monthly calls
GRADE_RETRY = timedelta(hours=3)       # grading always costs 2 calls
SEASON_EVERY = timedelta(days=7)
LOOKBACK = timedelta(days=5)
KEEP_CALLS_PROJECT = 60                # don't auto-project below this many calls left
KEEP_CALLS_GRADE = 10


def config_value(name: str, default: int) -> int:
    match = re.search(rf"^{name}:\s*(\d+)", CONFIG.read_text(encoding="utf-8"), re.MULTILINE)
    return int(match.group(1)) if match else default


def season() -> int:
    """The season the model projects (config.yaml `season:`)."""
    return config_value("season", datetime.now().year)


def budget() -> tuple[int, int]:
    """(calls used this month, monthly limit), from the model's own call counter."""
    limit = int(re.search(r"monthly_call_budget:\s*(\d+)", CONFIG.read_text(encoding="utf-8")).group(1))
    try:
        counter = json.loads(COUNTER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        counter = {}
    period = datetime.now(timezone.utc).strftime("%Y-%m")  # the counter's own month (UTC)
    return (int(counter.get("count", 0)) if counter.get("period") == period else 0), limit


def schedule(year: int) -> pd.DataFrame | None:
    """FBS games from the model's cached /games pull: id, week, kickoff (UTC). None if not cached."""
    files = sorted(CACHE.glob(f"games__year{year}_seasonTyperegular__*.parquet"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    games = pd.read_parquet(files[-1], columns=["id", "week", "startDate",
                                                "homeClassification", "awayClassification"])
    games = games[(games["homeClassification"] == "fbs") | (games["awayClassification"] == "fbs")].copy()
    games["kickoff"] = pd.to_datetime(games["startDate"], utc=True, errors="coerce")
    return games


def projections_path(year: int, week: int):
    return OUT / f"projections_{year}_week_{week}.csv"


def ungraded(year: int, now: datetime) -> int:
    """Projected games that have finished (recently) but aren't in the results log yet."""
    graded = set()
    if RESULTS.exists():
        log = pd.read_csv(RESULTS, dtype={"game_id": str})
        graded = set(log.loc[log["season"] == year, "game_id"])
    pending = 0
    for path in OUT.glob(f"projections_{year}_week_*.csv"):
        rows = read_rows(path)
        for game_id, start in rows[["game_id", "start_date"]].itertuples(index=False):
            kickoff = to_utc(start)
            if (kickoff is not None and now - LOOKBACK <= kickoff <= now - GAME_LENGTH
                    and game_id not in graded):
                pending += 1
    return pending


def plan(history: History, force: bool) -> Plan:
    now = utcnow()
    year = season()
    used, limit = budget()
    left = limit - used
    p = Plan("cfb", label=f"{year} season")
    p.stat("CFBD API", f"{used:,} / {limit:,}", "calls this month",
           tone="bad" if left < 150 else None)

    pending = ungraded(year, now)
    tried = history.last("grade", "attempt")
    retry_wait = tried is not None and now - tried < GRADE_RETRY
    if not pending:
        due, reason = force, "nothing new to grade"
    elif left < KEEP_CALLS_GRADE:
        due, reason = False, f"{plural(pending, 'finished game')} to grade, but only {left} API calls left"
    else:
        due = force or not retry_wait
        reason = f"{plural(pending, 'finished game')} to grade (2 API calls)" + (
            f"; tried {ago(tried, now)}" if retry_wait else "")
    p.step("grade", "Grade finished games", ["grade"], due=due, reason=reason)

    games = schedule(year)
    if games is None:
        p.notes.append(f"No cached {year} schedule yet - run a projection once to fetch it.")
        week = None
    else:
        stamp = pd.Timestamp(now)
        live = games[games["kickoff"] + GAME_LENGTH > stamp]
        week = int(live["week"].min()) if len(live) else None
    if week is None:
        p.step("project", "Project the week", [], due=False,
               reason="no schedule cached" if games is None else "regular season over")
        return p

    p.label = f"Week {week}"
    upcoming = int((games[games["week"] == week]["kickoff"] > pd.Timestamp(now)).sum())
    written = file_time(projections_path(year, week))
    if not upcoming:
        due, reason = False, f"every week-{week} game has kicked off"
    elif left < KEEP_CALLS_PROJECT and not force:
        due, reason = False, f"saving the last {left} API calls this month"
    elif written is None:
        due, reason = True, f"week {week} not projected yet"
    elif now - written >= PROJECT_EVERY:
        due, reason = True, f"last projected {ago(written, now)}"
    else:
        due, reason = force, f"projected {ago(written, now)} (refreshes every {span(PROJECT_EVERY)})"
    p.step("project", f"Project week {week}", ["project", "--week", week], due=due, reason=reason)

    season_written = file_time(OUT / f"season_{year}_projected_records.csv")
    stale = season_written is None or now - season_written >= SEASON_EVERY
    p.step("season", "Season sim", ["season"], due=force or due or stale,
           reason="after new projections" if due else f"last run {ago(season_written, now)}",
           requires=("project",) if due else ())
    if left < 150:
        p.notes.append(f"Only {left} CollegeFootballData calls left this month.")
    need_inputs(p, year, week, now)
    return p


RETRAIN_EVERY = timedelta(days=21)  # "every few weeks is plenty" (README)


def need_inputs(p: Plan, year: int, week: int, now: datetime) -> None:
    """The checklist of what only the user can give the model (the app's "Your inputs")."""
    import yaml  # the model's own dependency, for config.yaml

    overrides = (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("overrides") or {}
    edited = file_time(CONFIG)
    week_began = last_weekday(now, 0)  # Monday morning: last week's adjustments are stale by then
    p.need("overrides", "Injury and news adjustments", "Thu–Sat, before kickoff",
           detail=("CFBD has no injury data: in config.yaml, overrides: {Team: points}, e.g. Alabama: -4.5 for a "
                   "starting QB out; then Run now on College Football. Clear them each week."
                   + (f" Now set: {', '.join(f'{team} {value:+g}' for team, value in overrides.items())}."
                      if overrides else "")),
           last=edited if overrides else None,
           due=(edited is not None and edited < week_began) if overrides else None,
           again=week_began + timedelta(days=7))  # Done (none needed this week): back next Monday

    trained = None
    try:
        trained = to_utc(json.loads((FOLDER / "models" / "current" / "metadata.json").read_text())["saved_at"])
    except (OSError, ValueError, KeyError):
        pass
    p.need("retrain", "Retrain the model", "every few weeks",
           detail="Folds this season's results into the model (a few API calls)",
           how=".venv\\Scripts\\python -m cfb_model.backtest --train --rebuild",
           last=trained, due=trained is None or now - trained >= RETRAIN_EVERY)

    imported = FOLDER / "data" / "fantasydata" / f"{year}_week_{week}_games.csv"
    p.need("fantasydata", f"FantasyData projections, week {week} (optional)", "before the week's games",
           detail="QB, RB, WR, TE projection CSVs from FantasyData; they only drive the QB alerts, never the model",
           how=".venv\\Scripts\\python -m cfb_model.fantasy <folder with the CSVs>",
           last=file_time(imported), due=False if imported.exists() else None,
           again=week_began + timedelta(days=7))


# --- Steps ----------------------------------------------------------------------------------------

def freeze_for(path: Path) -> Freeze | None:
    """The freeze for a week's projections file; each row carries its kickoff (start_date)."""
    if not re.fullmatch(r"projections_\d{4}_week_\d+\.csv", path.name):
        return None
    return Freeze("cfb", path, key=["game_id"], kickoff=lambda row: to_utc(row.get("start_date")))


def step_project(args: argparse.Namespace) -> int:
    if args.week is None:
        return finish(False, "project needs --week")
    year, week = season(), args.week
    path = projections_path(year, week)
    freeze = freeze_for(path)
    used_before, _ = budget()
    start = utcnow()
    freeze.hold(start)
    code = run([PY, "-m", "cfb_model.project", "--week", week, "--no-color"], FOLDER)
    calls = budget()[0] - used_before
    if code != 0:
        restored = freeze.undo()
        return finish(False, f"project exited with code {code}"
                      + ("; the previous projections were put back" if restored else ""))
    if not freeze.rewritten:
        freeze.release()
        return finish(False, f"the model didn't write {path.name} (no week-{week} games?)")

    counts = freeze.settle()
    rows = read_rows(path)
    if rows.empty:
        restored = freeze.undo()
        return finish(False, "the model wrote no projections"
                      + ("; the previous file was put back" if restored else ""))
    parts, warnings = freeze_notes(counts)
    if "market_margin" in rows and not (rows["market_margin"] != "").any():
        warnings.append("no betting lines came back, so there are no spread or total picks")
    parts.append(f"{plural(calls, 'API call')}" if calls > 0 else "no API calls (cache was fresh)")
    return finish(True, "; ".join([f"Projected week {week}: {plural(len(rows), 'game')}"] + parts), warnings)


def step_season(args: argparse.Namespace) -> int:
    year = season()
    start = utcnow()
    code = run([PY, "-m", "cfb_model.season"], FOLDER)
    if code != 0:
        return finish(False, f"season exited with code {code}")
    records = OUT / f"season_{year}_projected_records.csv"
    if not written_since(records, start):
        return finish(False, f"the season sim didn't write {records.name}")
    return finish(True, f"Simulated the {year} season")


def step_grade(args: argparse.Namespace) -> int:
    year = season()
    before = _graded(year)
    code = run([PY, "-m", "cfb_model.grade", "--all"], FOLDER)
    if code != 0:
        return finish(False, f"grade exited with code {code}")
    after = _graded(year)
    new = after - before
    return finish(True, f"Graded {plural(after, 'game')} this season" + (f" ({new} new)" if new > 0 else ""))


def _graded(year: int) -> int:
    if not RESULTS.exists():
        return 0
    log = pd.read_csv(RESULTS, usecols=["season"])
    return int((log["season"] == year).sum())


if __name__ == "__main__":
    raise SystemExit(cli("cfb", plan, {"project": step_project, "season": step_season, "grade": step_grade},
                         freeze_for))
