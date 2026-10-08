"""NHL for the desktop app: what to run when, and each step run with the kickoff freeze.

Runs with the NHL model's venv Python:
  python desktop/sports/nhl.py plan            # what's due now (runs nothing)
  python desktop/sports/nhl.py step daily      # predicts the next game day
  python desktop/sports/nhl.py step simulate
  python desktop/sports/nhl.py step power
  python desktop/sports/nhl.py step rebuild --date 2026-10-03

`nhl daily` rewrites the day's outputs/daily/<date>_predictions.csv each run,
leaving games that have already started as empty rows - and the site grades
from that file - so the freeze puts back each started game's earlier rows
(predictions and flagged bets alike).

A game that started before any run predicted it gets numbers rebuilt as of a
minute before its puck drop, for the Daily tab only: they go to
<date>_rebuilt.csv, which nothing grades (see rebuild()).
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from itertools import groupby
from pathlib import Path

import pandas as pd

from _common import (ET, Freeze, History, Plan, ago, cli, et_day, file_time, finish, freeze_notes,
                     model_entry, plural, read_rows, run, span, to_utc, utcnow, written_since)

MODEL = model_entry("nhl")
FOLDER = MODEL["folder"]
NHL = MODEL["python"].with_name("nhl.exe")
OUT = FOLDER / "outputs"
DAILY = OUT / "daily"

DAILY_EVERY = timedelta(minutes=30)          # free NHL API; starters get confirmed through the day
DAILY_EVERY_WITH_ODDS = timedelta(hours=3)   # each run spends 3 of The Odds API's 500 free monthly credits
# The app keeps ODDS_API_KEY from the model unless models.toml says `paid_odds = true`, so the
# model spends no Odds API credits by default. Its config's `odds_source: espn` needs no key.
PAID_ODDS = bool(MODEL.get("paid_odds", False))
HIDE = () if PAID_ODDS else ("ODDS_API_KEY",)


def odds_source() -> str:
    """Where the model's live odds come from (its config's odds_source): 'espn' (free) or
    'the_odds_api' (credits) - the only source before the setting existed."""
    try:
        import yaml  # in the model's venv
        config = yaml.safe_load((FOLDER / "config" / "nhl.yaml").read_text(encoding="utf-8"))
        return config.get("odds_source", "the_odds_api")
    except Exception:
        return "the_odds_api"
SIM_EVERY = timedelta(hours=20)
SIMS = {"simulate": ("Season sim", OUT / "season_sim.csv"),
        "power": ("Power rankings", OUT / "power_rankings.csv")}


def plan(history: History, force: bool) -> Plan:
    now = utcnow()
    p = Plan("nhl")
    days = sorted(DAILY.glob("????-??-??_predictions.csv"))
    if days:
        p.label = et_day(to_utc(days[-1].name[:10] + "T12:00:00"))

    free = odds_source() == "espn"
    odds = not free and PAID_ODDS and bool(os.environ.get("ODDS_API_KEY"))  # a run spends credits
    every = DAILY_EVERY_WITH_ODDS if odds else DAILY_EVERY
    # The newest predictions file counts too: a run from update-data.ps1 or the terminal.
    runs = [t for t in [history.last("daily"), *(file_time(d) for d in days[-3:])] if t]
    last = max(runs) if runs else None
    tried = history.last("daily", "attempt")
    if odds and tried is not None and (last is None or tried > last) and now - tried < every:
        # The last try failed: with live odds each try spends credits, so no hourly retries.
        due, reason = force, (f"last try failed {ago(tried, now)}; tries again after {span(every)} "
                              "(each try spends 3 Odds API credits)")
    elif last is None:
        due, reason = True, "no predictions yet"
    elif now - last >= every:
        due, reason = True, f"last run {ago(last, now)}"
    else:
        due, reason = force, f"ran {ago(last, now)} (refreshes every {span(every)})"
    p.step("daily", "Predict the next game day", ["daily"], due=due, reason=reason)

    for step, (label, out) in SIMS.items():
        written = file_time(out)
        stale = written is None or now - written >= SIM_EVERY
        p.step(step, label, [step], due=force or due or stale,
               reason="after new predictions" if due else f"last run {ago(written, now)}",
               requires=("daily",) if due else ())

    p.stat("Betting lines", "On" if free or odds else "Off",
           "ESPN (DraftKings), free" if free else "The Odds API" if odds else
           "no ODDS_API_KEY" if PAID_ODDS else "paid odds off (models.toml)")
    return p


# --- Steps ----------------------------------------------------------------------------------------

def predicted(row: dict) -> bool:
    return row.get("p_home_win", "") != ""


def freeze_for(path: Path) -> Freeze | None:
    """The freeze for a day's predictions (a started game keeps its earlier row) or its flagged
    bets (a started game keeps its bets; the run may drop a bet on a game still to come)."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}_predictions\.csv", path.name):
        return Freeze("nhl", path, key=["game_id"], predicted=predicted,
                      kickoff=lambda row: to_utc(row.get("start_ts")))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}_bets\.csv", path.name):
        games = path.with_name(path.name.replace("_bets", "_predictions"))
        starts: dict = {}  # read when the freeze settles, from the day's new predictions

        def kickoff(row: dict):
            if not starts and games.exists():
                starts.update({r["game_id"]: to_utc(r.get("start_ts")) for r in read_rows(games).to_dict("records")})
            return starts.get(row.get("game_id"))

        return Freeze("nhl", path, key=["game_id"], carry_unstarted=False, kickoff=kickoff)
    return None


def step_daily(args: argparse.Namespace) -> int:
    start = utcnow()
    today = start.astimezone(ET).date()
    freezes = []  # yesterday's files onward: predictions first, so the bets can read their kickoffs
    for path in sorted(DAILY.glob("????-??-??_predictions.csv")):
        if date.fromisoformat(path.name[:10]) >= today - timedelta(days=1):
            freezes += [freeze_for(path), freeze_for(path.with_name(path.name.replace("_predictions", "_bets")))]
    for freeze in freezes:
        freeze.hold(start)

    code = run([NHL, "daily"], FOLDER, hide=HIDE)
    if code != 0:
        restored = sum(freeze.undo() for freeze in freezes)
        return finish(False, f"nhl daily exited with code {code}"
                      + (f"; put back {restored} earlier files" if restored else ""))

    written = [p for p in sorted(DAILY.glob("????-??-??_predictions.csv")) if written_since(p, start)]
    if not written:
        for freeze in freezes:
            freeze.release()
        return finish(False, "nhl daily didn't write a predictions file")

    counts = {"kept": 0, "dropped": 0, "carried": 0, "unknown": 0}
    for freeze in freezes:
        if freeze.path.name.endswith("_predictions.csv"):
            for k, v in freeze.settle().items():
                counts[k] += v
        else:
            freeze.settle()  # bets follow their games; reported with the predictions
    parts, warnings = freeze_notes(counts)

    # Games no run predicted before puck drop: rebuild numbers to show (never graded).
    for path in written:
        try:
            rebuilt = rebuild(path.name[:10])
        except Exception as error:  # display-only: never fail the day's predictions over it
            warnings.append(f"couldn't rebuild the games that started unpredicted: {error}")
            continue
        if rebuilt:
            parts.append(f"rebuilt {plural(rebuilt, 'game')} that started before any run (shown, not graded)")

    day = written[-1]
    rows = read_rows(day)
    made = sum(predicted(r) for r in rows.to_dict("records"))
    label = et_day(to_utc(day.name[:10] + "T12:00:00"))
    if made == 0:
        warnings.append("no game on the day got a prediction")
    return finish(True, "; ".join([f"{label}: {made} of {len(rows)} games predicted"] + parts), warnings)


def rebuild(day: str) -> int:
    """Numbers for the day's games that started before any run predicted them, made as of a
    minute before each puck drop, appended to outputs/daily/<day>_rebuilt.csv. Returns how
    many games it rebuilt.

    It runs the model's own daily pipeline offline, with its clock set back, on a throwaway
    copy of the database - the real one (its predictions, bets and record) is never touched.
    The model only uses what was known before its clock: ratings from earlier days, goalie
    lists and starters stamped before then. Its one deliberate exception, a game's actual
    starter (for backtests), is deleted from the copy first. Still, these were made after the
    fact, so they are only shown: the site grades <day>_predictions.csv, never this file.
    """
    path = DAILY / f"{day}_predictions.csv"
    out = DAILY / f"{day}_rebuilt.csv"
    if not path.exists():
        return 0
    done = set(read_rows(out)["game_id"]) if out.exists() else set()
    missing = [r for r in read_rows(path).to_dict("records") if not predicted(r) and r["game_id"] not in done]
    started = [r for r in missing if (to_utc(r.get("start_ts")) or utcnow()) <= utcnow()]
    if not started:
        return 0

    sys.path.insert(0, str(FOLDER))  # the model's own packages
    from core.config import PROJECT_ROOT, load_config
    from core.data import db
    from pipelines import daily_run

    config = load_config(PROJECT_ROOT / "config" / "nhl.yaml")
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "nhl.sqlite"
        source = sqlite3.connect(f"{config.path('paths.db').as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.close()
        conn = db.connect(copy)
        try:
            ids = [int(r["game_id"]) for r in started]
            conn.execute(f"DELETE FROM goalie_starts WHERE status = 'actual' AND game_id IN "
                         f"({','.join('?' * len(ids))})", ids)
            # One pass per puck-drop time, each as of a minute before it.
            for start_ts, group in groupby(sorted(started, key=lambda r: r["start_ts"]), key=lambda r: r["start_ts"]):
                as_of = db.format_ts(db.parse_ts(start_ts) - timedelta(minutes=1))
                result = daily_run.run(conn, config, day, clock=lambda: as_of, season_odds=False)
                table = daily_run.predictions_table(result)
                table = table[table["game_id"].astype(str).isin({r["game_id"] for r in group})].copy()
                table["rebuilt_as_of"] = as_of
                frames.append(table)
        finally:
            conn.close()
    new = pd.concat(frames, ignore_index=True)
    if out.exists():
        new = pd.concat([pd.read_csv(out), new], ignore_index=True)
    new.to_csv(out, index=False, lineterminator="\n")
    return len(started)


def step_rebuild(args: argparse.Namespace) -> int:
    day = args.date or utcnow().astimezone(ET).date().isoformat()
    count = rebuild(day)
    label = et_day(to_utc(day + "T12:00:00"))
    return finish(True, f"{label}: rebuilt {plural(count, 'game')} that started before any run (shown, not graded)"
                  if count else f"{label}: no started game is missing a prediction")


def step_sim(name: str):
    label, out = SIMS[name]

    def step(args: argparse.Namespace) -> int:
        start = utcnow()
        code = run([NHL, name], FOLDER)
        if code != 0:
            return finish(False, f"nhl {name} exited with code {code}")
        if not written_since(out, start):
            return finish(False, f"nhl {name} didn't write {out.name}")
        return finish(True, f"{label} updated")

    return step


if __name__ == "__main__":
    raise SystemExit(cli("nhl", plan, {"daily": step_daily, "simulate": step_sim("simulate"),
                                       "power": step_sim("power"), "rebuild": step_rebuild}, freeze_for))
