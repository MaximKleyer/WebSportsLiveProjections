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
import csv
import difflib
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from _common import (ET, Freeze, History, Plan, ago, cli, file_time, finish, freeze_notes, last_weekday,
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
RAW = FOLDER / "data" / "raw"
# The weekly FantasyPoints exports build_database.py reads, by these exact names (database.py).
EXPORTS = ("advanced_passing_player.csv", "advanced_passing_def.csv", "advanced_rushing_player.csv",
           "advanced_rushing_def.csv", "advanced_receiving_player.csv")
# ...and these when there (receiving defense isn't read by any projection; injuries: their own item).
STATS = EXPORTS + ("advanced_receiving_def.csv",)
WEEKLY = STATS + ("injury_report.csv",)
# A column every view of a family has, in the old exports and Data Suite 2.0 alike; the player views
# also have POS, which the defense views don't.
FAMILY_MARK = {"passing": "CMP", "rushing": "YPC", "receiving": "TGT"}
FIRST_SEASON = 2021  # the warehouse's first season

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
    need_inputs(p, season, games, week, now)
    return p


def misnamed(folder: Path) -> dict[str, list[str]]:
    """{file in `folder`: the name it looks like a misspelling of}, among the WEEKLY names the folder
    lacks ("advanced_recieving_player.csv"): the build reads exact names and skips a look-alike.
    Both names when it's about as close to two ("advanced_receiving.csv": player or defense view?)."""
    if not folder.is_dir():
        return {}
    absent = [name for name in WEEKLY if not (folder / name).exists()]
    known = {name.lower() for name in WEEKLY}
    found = {}
    for path in sorted(folder.glob("*.csv")):
        have = path.name.lower()
        scores = sorted(((difflib.SequenceMatcher(None, have, name).ratio(), name) for name in absent),
                        reverse=True)
        close = [name for score, name in scores if score >= 0.8 and score > scores[0][0] - 0.08]
        if close and have not in known:
            found[path.name] = close
            if len(close) == 1:
                absent.remove(close[0])
    return found


def renames(found: dict[str, list[str]]) -> str:
    """'rename A to B, C to D or E' for the files the build would skip under their names, or ''."""
    if not found:
        return ""
    return ("rename " + ", ".join(f"{have} to {' or '.join(want)}" for have, want in found.items())
            + " (the model reads files by their exact names)")


def export_header(path: Path) -> tuple[int, list[str]]:
    """The row a FantasyPoints export's header is on, and its cells, found the way the model finds it
    (ingest/readers.py header_row): the first of the top two rows with a Rank / # / Season cell -
    Data Suite 2.0 puts a group row above it."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        top = [[cell.strip() for cell in row] for _, row in zip(range(2), csv.reader(fh))]
    for i, row in enumerate(top):
        if {"RANK", "#", "SEASON"} & {cell.upper() for cell in row}:
            return i, row
    return 0, top[0] if top else []


def export_fault(path: Path) -> tuple[str, str]:
    """What's wrong inside one weekly export, as (kind, what) - ("", "") when nothing is: "view" for
    the wrong report (a player view saved as the defense one, a rushing view as receiving...), "span"
    with the top G for more than one week in it (season to date: the build would count the earlier
    weeks twice), "empty" or "read" for no rows or a file that can't be read."""
    family = path.stem.split("_")[1]
    player = path.stem.endswith("_player")
    try:
        header, columns = export_header(path)
        if player and "POS" not in columns:
            return "view", "is a defense view, not the player one"
        if not player and "POS" in columns:
            return "view", "is the player view, not the defense one"
        if FAMILY_MARK[family] not in columns:
            return "view", f"isn't the {family} view (it has no {FAMILY_MARK[family]} column)"
        label = next((c for c in ("Name", "Player") if c in columns), None)
        rows = pd.read_csv(path, encoding="utf-8-sig", header=header, dtype=str, keep_default_na=False,
                           usecols=[c for c in (label, "G") if c in columns])
    except Exception as exc:  # noqa: BLE001 - a half-saved file, one open in Excel...
        return "read", f"can't be read ({exc})"
    if label is not None:  # not the "League Avg" row (or a blank one), as the model drops them
        text = rows[label].str.strip()
        rows = rows[(text != "") & ~text.str.lower().str.startswith("league avg")]
    if rows.empty:
        return "empty", "has no rows"
    games = pd.to_numeric(rows["G"], errors="coerce").max() if "G" in rows.columns else None
    if games is not None and games > 1:
        return "span", f"{games:g}"
    return "", ""


def week_faults(folder: Path) -> dict[str, tuple[str, str]]:
    """{export: export_fault} for the week's exports that are there and aren't right."""
    faults = {}
    for name in STATS:
        if (folder / name).exists() and (fault := export_fault(folder / name))[0]:
            faults[name] = fault
    return faults


def listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def week_problem(folder: Path, week: int, faults: dict[str, tuple[str, str]]) -> str:
    """What's wrong with a finished week's exports so far: files to rename, files still missing, and
    the `faults` inside the ones that are there. '' when nothing is, or nothing's there yet (the item
    is due, which says as much)."""
    found = misnamed(folder)
    claimed = {name for want in found.values() for name in want}
    absent = [name for name in EXPORTS if not (folder / name).exists() and name not in claimed]
    parts = [renames(found)] if found else []
    if absent and (parts or faults or len(absent) < len(EXPORTS)):
        parts.append("missing " + ", ".join(absent))
    spans = [name for name, (kind, _) in faults.items() if kind == "span"]
    if spans:
        top = max(float(faults[name][1]) for name in spans)
        parts.append(f"{listed(spans)} {'is' if len(spans) == 1 else 'are'} season to date (G up to {top:g}): "
                     f"re-export {'it' if len(spans) == 1 else 'them'} with the week filter on week {week} alone")
    parts += [f"{name} {what}" for name, (kind, what) in faults.items() if kind != "span"]
    return f"Week {week}: " + "; ".join(parts) if parts else ""


def need_inputs(p: Plan, season: int, games: pd.DataFrame, week: int | None, now: datetime) -> None:
    """The checklist of what only the user can give the model (the app's "Your inputs"):
    the week's FantasyPoints exports, the injury report, the warehouse rebuild, QB changes."""
    stamp = pd.Timestamp(now)
    regular = games[games["week"] <= 18]  # playoff folders are named, not numbered (README)
    finished = [int(w) for w, last in regular.groupby("week")["kickoff"].max().items()
                if last + GAME_LENGTH <= stamp]
    folder = lambda w: RAW / str(season) / f"week_{w:02d}"  # noqa: E731
    # Every finished week, each time: a wrong file stays on the list until it's replaced, built or not.
    faults = {w: week_faults(folder(w)) for w in finished}
    todo = [w for w in finished
            if faults[w] or not all((folder(w) / name).exists() for name in EXPORTS)]
    if finished:
        shown = todo[0] if todo else finished[-1]
        saved = [file_time(folder(shown) / name) for name in EXPORTS]
        p.need("exports", f"FantasyPoints stats, week{'s' if len(todo) > 1 else ''} "
               + (", ".join(map(str, todo)) if todo else str(finished[-1])),
               "Tuesdays, after Monday night",
               detail=(f"Data Suite 2.0: Advanced Passing, Rushing and Receiving, player and defense views, season "
                       f"{season}, the week filter on that one week (not season to date) -> data\\raw\\{season}\\"
                       f"week_NN\\ as {', '.join(STATS)}"),
               how=f".venv\\Scripts\\python -m nfl_projector_v1 check-export data/raw/{season}/week_{shown:02d}",
               last=None if None in saved else max(saved), due=bool(todo),
               problem=". ".join(filter(None, (week_problem(folder(w), w, faults[w]) for w in todo))))

    if week is not None and week <= 18:
        days = games.loc[games["week"] == week, "kickoff"].dt.tz_convert(ET).dt.date
        sunday = days.mode().iloc[0]  # the week's main game day
        friday = datetime.combine(sunday - timedelta(days=2), datetime.min.time(), ET).replace(hour=6)
        report = folder(week) / "injury_report.csv"
        written = file_time(report)
        wrong = renames({have: [report.name] for have, want in misnamed(report.parent).items()
                         if report.name in want})
        p.need("injuries", f"Injury report, week {week}",
               f"{friday:%a %b} {friday.day} or Sat, once it's final",
               detail=f"Data Suite: Injury Database -> data\\raw\\{season}\\week_{week:02d}\\injury_report.csv "
                      "(its Questionable, Doubtful and Out override the nflverse feed); then rebuild",
               # Before Friday any report in the week's folder will do for now; from then, only the final one.
               last=written, check=True,
               due=(False if written else None) if now < friday else written is None or written < friday,
               problem=f"Week {week}: {wrong}" if wrong else "")

    built = file_time(WAREHOUSE)
    exported = [file_time(f) for f in (RAW / str(season)).glob("week_*/*.csv")]
    newest = max((t for t in exported if t), default=None)
    seasons = " ".join(str(s) for s in range(FIRST_SEASON, season + 1))  # they overwrite: every season
    held = [w for w in finished if faults[w]]
    p.need("rebuild", "Rebuild the warehouse", "after new exports or an injury report",
           detail="In models\\nfl, these in order (the fetch and build take every season), then Run now on NFL",
           how=(f".venv\\Scripts\\python scripts\\fetch_external_data.py --seasons {seasons}; "
                f".venv\\Scripts\\python scripts\\build_database.py --seasons {seasons}; "
                f".venv\\Scripts\\python -m nfl_projector_v1 refresh-depth-charts --seasons {season}"),
           last=built, due=built is None or (newest is not None and newest > built),
           problem=(f"Not yet: week{'s' if len(held) > 1 else ''} {listed([str(w) for w in held])}'s exports "
                    "aren't right (above), and the build reads them as they are") if held else "")

    p.need("qbs", "QB changes", "as news breaks",
           detail="A benching, or a starter ruled out: name the week's QB in nfl_projector_v1\\qb_starters.yaml "
                  "(season -> week -> TEAM: QB). The predict log flags doubtful starters as QB WATCH.",
           again=last_weekday(now, 1) + timedelta(days=7))  # Done: back next Tuesday


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
