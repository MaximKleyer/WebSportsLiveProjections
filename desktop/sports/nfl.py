"""NFL for the desktop app: what to run when, and each step run with the kickoff freeze.

Runs with the NFL model's venv Python:
  python desktop/sports/nfl.py plan                          # what's due now (runs nothing)
  python desktop/sports/nfl.py step predict --season 2026 --week 5
  python desktop/sports/nfl.py step season --season 2026
  python desktop/sports/nfl.py step grade --season 2026

The schedule (weeks, kickoffs) comes from the nflverse games feed the model
caches at data/raw/external/games_latest.csv; grading refreshes it.
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from _common import (ET, Freeze, History, Plan, ago, cli, file_time, finish, freeze_notes,
                     model_entry, plural, read_rows, run, span, utcnow, written_since)

MODEL = model_entry("nfl")
FOLDER, PY = MODEL["folder"], MODEL["python"]
OUT = FOLDER / "data" / "processed" / "v2"
FEED = FOLDER / "data" / "raw" / "external" / "games_latest.csv"
RESULTS = OUT / "results_log.csv"
WAREHOUSE = FOLDER / "data" / "processed" / "warehouse.duckdb"

GAME_LENGTH = timedelta(hours=4)       # kickoff to final, for "has this game finished?"
PREDICT_EVERY = timedelta(hours=12)    # inputs are free (weather, the nflverse feed)
GRADE_RETRY = timedelta(hours=2)       # when finished games aren't in the feed yet
SEASON_EVERY = timedelta(days=7)
LOOKBACK = timedelta(days=5)           # older ungraded games aren't worth re-grading for
PLAYER_DATA_STALE = timedelta(days=8)

# The feed spells a few teams differently from the model (nfl_projector_v1/results.py).
TEAM_ALIASES = {"LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV", "WSH": "WAS"}


def season_of(now: datetime) -> int:
    """January-February games (the playoffs) belong to the season that began the year before."""
    local = now.astimezone(ET)
    return local.year if local.month >= 3 else local.year - 1


def schedule(season: int) -> pd.DataFrame:
    """The season's games from the cached feed: week, away_team, home_team, kickoff (UTC)."""
    feed = pd.read_csv(FEED, low_memory=False,
                       usecols=["season", "week", "gameday", "gametime", "away_team", "home_team"])
    games = feed[feed["season"] == season].copy()
    for side in ("away_team", "home_team"):
        games[side] = games[side].replace(TEAM_ALIASES)
    local = pd.to_datetime(games["gameday"] + " " + games["gametime"].fillna("13:00"), errors="coerce")
    games["kickoff"] = (local.dt.tz_localize(ET, ambiguous="NaT", nonexistent="shift_forward")
                        .dt.tz_convert("UTC"))
    return games


def predictions_path(season: int, week: int):
    return OUT / f"predictions_{season}_week{week:02d}.csv"


def ungraded(season: int, games: pd.DataFrame, now: datetime) -> int:
    """Projected games that have finished (recently) but aren't in the results log yet."""
    cols = ["week", "away_team", "home_team"]  # selected after reading: usecols keeps file order
    projected = set()
    for path in OUT.glob(f"predictions_{season}_week??.csv"):  # not the *_rerun.csv copies
        df = pd.read_csv(path)
        projected |= {(int(w), a, h) for w, a, h in df[cols].itertuples(index=False)}
    graded = set()
    if RESULTS.exists():
        log = pd.read_csv(RESULTS)
        log = log[log["season"] == season]
        graded = {(int(w), a, h) for w, a, h in log[cols].itertuples(index=False)}
    stamp = pd.Timestamp(now)
    done = games[(games["kickoff"] + GAME_LENGTH <= stamp) & (games["kickoff"] >= stamp - LOOKBACK)]
    finished = {(int(w), a, h) for w, a, h in done[["week", "away_team", "home_team"]].itertuples(index=False)}
    return len((projected & finished) - graded)


def plan(history: History, force: bool) -> Plan:
    now = utcnow()
    season = season_of(now)
    p = Plan("nfl", label=f"{season} season")
    if not FEED.exists():
        p.notes.append("No cached schedule yet: grading downloads it, then the next run can project.")
        p.step("grade", "Grade finished games", ["grade", "--season", season], due=True,
               reason="fetches the schedule")
        return p

    games = schedule(season)
    stamp = pd.Timestamp(now)
    live = games[games["kickoff"] + GAME_LENGTH > stamp]
    week = int(live["week"].min()) if len(live) else None

    # Grade first: it also refreshes the feed the projections read.
    pending = ungraded(season, games, now)
    tried = history.last("grade", "attempt")
    retry_wait = tried is not None and now - tried < GRADE_RETRY
    p.step("grade", "Grade finished games", ["grade", "--season", season],
           due=force or (pending > 0 and not retry_wait),
           reason=(f"{plural(pending, 'finished game')} to grade" + (f" (tried {ago(tried, now)})" if retry_wait else ""))
           if pending else "nothing new to grade")

    if week is None:
        p.label = f"{season} season over"
        p.step("predict", "Project the week", [], due=False, reason="no games left this season")
    else:
        p.label = f"Week {week}"
        upcoming = int((games[(games["week"] == week)]["kickoff"] > stamp).sum())
        written = file_time(predictions_path(season, week))
        if not upcoming:
            due, reason = False, f"every week-{week} game has kicked off"
        elif written is None:
            due, reason = True, f"week {week} not projected yet"
        elif now - written >= PREDICT_EVERY:
            due, reason = True, f"last projected {ago(written, now)}"
        else:
            due, reason = False, f"projected {ago(written, now)} (refreshes every {span(PREDICT_EVERY)})"
        due = due or (force and upcoming > 0)
        p.step("predict", f"Project week {week}", ["predict", "--season", season, "--week", week],
               due=due, reason=reason)
        predict_due = due

        season_file = OUT / f"season_standings_{season}.csv"
        season_written = file_time(season_file)
        stale = season_written is None or now - season_written >= SEASON_EVERY
        p.step("season", "Season sim", ["season", "--season", season],
               due=force or predict_due or stale,
               reason="after new projections" if predict_due
               else f"last run {ago(season_written, now)}",
               requires=("predict",) if predict_due else ())

    players = file_time(WAREHOUSE)
    if players is not None:
        p.stat("Player data", players.astimezone(ET).strftime("%b %d").replace(" 0", " "),
               "FantasyPoints exports", tone="bad" if now - players > PLAYER_DATA_STALE else None)
        if now - players > PLAYER_DATA_STALE:
            p.notes.append(f"Player data was last rebuilt {ago(players, now)}; the app can't refresh it "
                           "(it comes from FantasyPoints exports you download).")
    return p


# --- Steps ----------------------------------------------------------------------------------------

def week_games(season: int, week: int) -> pd.DataFrame:
    if not FEED.exists():
        return pd.DataFrame(columns=["week", "away_team", "home_team", "kickoff"])
    games = schedule(season)
    return games[games["week"] == week]


def freeze_for(path: Path) -> Freeze | None:
    """The freeze for a week's predictions file; kickoffs come from the schedule feed."""
    match = re.fullmatch(r"predictions_(\d{4})_week(\d{2})\.csv", path.name)
    if not match:
        return None
    games = week_games(int(match.group(1)), int(match.group(2)))
    kickoffs = {(a, h): k for a, h, k in games[["away_team", "home_team", "kickoff"]].itertuples(index=False)}
    return Freeze("nfl", path, key=["away_team", "home_team"], kickoff=lambda row: _kickoff(kickoffs, row))


def step_predict(args: argparse.Namespace) -> int:
    season, week = args.season, args.week
    if season is None or week is None:
        return finish(False, "predict needs --season and --week")
    path = predictions_path(season, week)
    games = week_games(season, week)
    freeze = freeze_for(path)
    start = utcnow()
    freeze.hold(start)
    code = run([PY, "-m", "nfl_projector_v1", "predict", "--season", season, "--week", week,
                "--players", "--plain"], FOLDER)
    if code != 0:
        restored = freeze.undo()
        return finish(False, f"predict exited with code {code}"
                      + ("; the previous projections were put back" if restored else ""))
    # Once any game of the week has a final score in the warehouse, the model leaves the saved
    # file alone and writes the run to *_rerun.csv - for the whole week, games still to come
    # included. The freeze does that game by game, so the run is merged in: games that have
    # kicked off keep the rows they had, the rest take this run's.
    rerun = path.with_name(f"{path.stem}_rerun.csv")
    merged = written_since(rerun, start) and not freeze.rewritten and path.exists()
    if merged:
        shutil.copyfile(rerun, path)

    counts = freeze.settle()
    projected = len(read_rows(path)) if path.exists() else 0
    if projected == 0:
        restored = freeze.undo()
        return finish(False, "the model wrote no projections (did every game fail?)"
                      + ("; the previous file was put back" if restored else ""))
    parts, warnings = freeze_notes(counts)
    if merged:
        parts.append(f"merged from {rerun.name} (week {week} already has a final score)")
    expected = len(games)
    if expected and projected < expected:
        warnings.append(f"only {projected} of {expected} week-{week} games have a projection")
    return finish(True, "; ".join([f"Projected week {week}: {plural(projected, 'game')}"] + parts), warnings)


def _kickoff(kickoffs: dict, row: dict):
    k = kickoffs.get((row["away_team"], row["home_team"]))
    return None if k is None or pd.isna(k) else k.to_pydatetime()


def step_season(args: argparse.Namespace) -> int:
    season = args.season
    if season is None:
        return finish(False, "season needs --season")
    start = utcnow()
    code = run([PY, "-m", "nfl_projector_v1", "predict-season", "--season", season], FOLDER)
    if code != 0:
        return finish(False, f"predict-season exited with code {code}")
    standings = OUT / f"season_standings_{season}.csv"
    if not written_since(standings, start):
        return finish(False, f"the season sim didn't write {standings.name}")
    return finish(True, f"Simulated the {season} season")


def step_grade(args: argparse.Namespace) -> int:
    season = args.season
    if season is None:
        return finish(False, "grade needs --season")
    before = _graded(season)
    code = run([PY, "-m", "nfl_projector_v1", "grade", "--season", season, "--all", "--refresh"], FOLDER)
    if code != 0:
        return finish(False, f"grade exited with code {code}")
    after = _graded(season)
    new = after - before
    return finish(True, f"Graded {plural(after, 'game')} this season" + (f" ({new} new)" if new > 0 else ""))


def _graded(season: int) -> int:
    if not RESULTS.exists():
        return 0
    log = pd.read_csv(RESULTS, usecols=["season"])
    return int((log["season"] == season).sum())


if __name__ == "__main__":
    raise SystemExit(cli("nfl", plan, {"predict": step_predict, "season": step_season, "grade": step_grade},
                         freeze_for))
