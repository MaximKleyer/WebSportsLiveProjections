#!/usr/bin/env python
"""NBA adapter: the NBA model's outputs -> the site's JSON (via site_export).

Reads the model's outputs/ - plus, for final scores and settled bets, its database
(read-only) - and writes public/data/nba/:

    games/          one slate per DAY           outputs/daily/<date>_predictions.csv
                                                (+ <date>_bets.csv: flagged bets)
    season.json     win totals + playoff odds   the newest outputs/sims/season_<season>_<date>.csv
    rankings.json   power rankings              outputs/power_rankings.csv
    results/        those daily predictions     final scores + settled bets from the
                    graded, one slate per day   model's database (data/nba.sqlite, read-only)
    card.json       the landing card's line

Only the live model version's predictions are published - the version outputs/model_state.json
was fitted for - so day files a rehearsal left behind with an earlier version stay off the site.
Before the first predicted day the games and results views are published empty (the site says
nothing is published yet).

The model's "NBA gameday" scheduled task writes the day files while games are on, rewriting them
in place, so a file caught mid-write is read again.

scripts/update-data.ps1 runs this with the model's own venv Python (pandas):

    <model>\\.venv\\Scripts\\python.exe scripts\\exporters\\nba.py [--day 2026-10-20]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from datetime import date
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_export import (  # noqa: E402  (the site's shared export toolkit)
    FLAGGED_FILTER, SITE_DATA, Slate, file_date, graded_cell, num, publish_slates, scoreboard,
    scoreboard_columns, spread, text, write_card, write_table,
)

MODEL = Path(r"C:\Users\maxim\WebSportsLiveProjections\models\nba")
EASTERN = ZoneInfo("America/New_York")
DAILY = re.compile(r"(?P<day>\d{4}-\d{2}-\d{2})_predictions\.csv$")
SIM = re.compile(r"season_(?P<season>\d{4})_(?P<day>\d{4}-\d{2}-\d{2})\.csv$")
SIM_COLUMNS = {"name", "conference", "wins_mean", "p_playoffs", "p_champion"}  # older sims lack them
# A tag under the tip-off time for anything but a regular-season game; preseason games are
# rehearsals - never bet, never graded.
GAME_TYPES = {"pre": "preseason", "playin": "play-in", "playoff": "playoffs", "cup_final": "Cup final"}
CONFERENCES = {"East": "Eastern Conference", "West": "Western Conference"}
SEARCH = "Search teams or players"


def _season_label(season: int) -> str:
    """2026 -> '2026–27' (the model's season id is the year it starts)."""
    return f"{season}–{(season + 1) % 100:02d}"


def _day_label(day: str) -> str:
    """'2026-10-20' -> 'Tue Oct 20'."""
    d = date.fromisoformat(day)
    return f"{d:%a} {d:%b} {d.day}"


def _season_of(game_id: str) -> int:
    """'0022600001' -> 2026: characters 4-5 of an NBA game id are its season."""
    return 2000 + int(str(game_id)[3:5])


def _fair_odds(p):
    """A win probability as American odds without a margin: 0.54 -> '-117'."""
    if p is None or not 0 < p < 1:
        return None
    return f"{(-100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p):+.0f}"


def _start(ts, game_type) -> dict | None:
    """'2026-10-20T23:30:00Z' -> a cell showing '7:30 PM' (ET) that sorts by the time,
    tagged 'preseason' / 'play-in' / ... for anything but a regular-season game."""
    if text(ts) is None:
        return None
    et = pd.Timestamp(ts).tz_convert(EASTERN)
    tag = GAME_TYPES.get(text(game_type) or "")
    return {"v": f"{et.hour % 12 or 12}:{et.minute:02d} {'AM' if et.hour < 12 else 'PM'}",
            "sort": text(ts), **({"sub": tag} if tag else {})}


def _listed(players, show: int = 2) -> str | None:
    """The injury report's rotation players for a team - 'J. Brown questionable, D. White out,
    S. Hauser probable' - cut to the first `show` (gravest first), plus how many more."""
    names = [p.strip() for p in (text(players) or "").split(",") if p.strip()]
    if not names:
        return None
    return ", ".join(names[:show]) + (f" +{len(names) - show}" if len(names) > show else "")


def _lean(p, line, yes: str, no: str) -> tuple[str, float] | None:
    """The side of a market line the model favours, with its probability: p is the chance of
    `yes` (the home side covering, the over). None at exactly 50% or without a line."""
    if p is None or line is None or p == 0.5:
        return None
    return (yes, p) if p > 0.5 else (no, 1 - p)


def _ats(d: dict, away: str, home: str):
    line = num(d.get("spread_line"))
    if line is None:
        return None
    return _lean(num(d.get("p_home_cover"), 4), line, f"{home} {spread(line)}", f"{away} {spread(-line)}")


def _ou(d: dict):
    line = num(d.get("total_line"))
    if line is None:
        return None
    return _lean(num(d.get("p_over"), 4), line, f"Over {line:g}", f"Under {line:g}")


def _bet_label(bet: dict, away: str, home: str) -> str:
    """'BOS ML', 'DET -2.5', 'Over 219.5' - as the model's own reports write a bet."""
    market, side, line = text(bet.get("market")), text(bet.get("side")), num(bet.get("line"))
    if market == "total":
        return f"{(side or '').title()} {'' if line is None else f'{line:g}'}".strip()
    team = home if side == "home" else away
    return f"{team} ML" if market == "ml" or line is None else f"{team} {line:+g}"


def read_csv_stable(path: Path, tries: int = 5) -> pd.DataFrame:
    """A CSV the gameday task may be rewriting right now: read it until the file stood still
    while it was read and every row is whole (a cut-off last row is missing its last field)."""
    for _ in range(tries):
        before = path.stat()
        try:
            df = pd.read_csv(path, dtype={"game_id": str})
        except (pd.errors.ParserError, pd.errors.EmptyDataError):
            df = None
        after = path.stat()
        still = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
        if df is not None and still and (df.empty or df.iloc[:, -1].notna().all()):
            return df
        time.sleep(1)
    raise SystemExit(f"{path.name} kept changing while it was read - try again in a minute")


def live_version(model: Path) -> str | None:
    """The model version outputs/model_state.json was fitted for: the one the site publishes."""
    try:
        return json.loads((model / "outputs" / "model_state.json").read_text(encoding="utf-8"))["model_version"]
    except (OSError, ValueError, KeyError):
        return None


# --------------------------------------------------------------------------
# games: one slate per day
# --------------------------------------------------------------------------
def build_day(df: pd.DataFrame, bets: pd.DataFrame, day: str, season: int, updated: str) -> dict:
    bets_by_game: dict = {}
    for b in bets.to_dict("records"):
        bets_by_game.setdefault(text(b.get("game_id")), []).append(b)
    has_market = any(df.get(c, pd.Series(dtype=float)).notna().any()
                     for c in ("market_home", "spread_line", "total_line"))

    rows = []
    for d in sorted(df.to_dict("records"), key=lambda d: text(d.get("start_ts")) or ""):
        away, home = text(d.get("away")), text(d.get("home"))
        am, hm = num(d.get("away_mean"), 2), num(d.get("home_mean"), 2)
        p_home = num(d.get("p_home_win"), 4)
        margin = None if am is None or hm is None else round(hm - am, 2)
        row = {
            "start": _start(d.get("start_ts"), d.get("game_type")),
            **scoreboard(away, home, am, hm),
            "homeWin": p_home,
            "fair": _fair_odds(p_home),
            # The model's own line, from the favourite's side: 'DET -1.3'.
            "spread": None if margin is None else {
                "v": "PK" if abs(margin) < 0.05 else f"{home if margin > 0 else away} -{abs(margin):.1f}",
                "sort": margin},
            "total": None if margin is None else {"v": f"{am + hm:.1f}", "sort": round(am + hm, 2)},
        }
        # Each side's rotation players on the injury report, under the team (searchable too).
        for key, side in (("awayTeam", "away"), ("homeTeam", "home")):
            listed = _listed(d.get(f"{side}_listed"))
            if listed and row[key]:
                row[key] = {"v": row[key], "sub": listed}
        if has_market:
            row["mktHome"] = num(d.get("market_home"), 4)
            for key, lean in (("ats", _ats(d, away, home)), ("ou", _ou(d))):
                row[key] = None if lean is None else {"v": f"{lean[0]} · {lean[1]:.0%}", "sort": round(lean[1], 4)}
        game_bets = bets_by_game.get(text(d.get("game_id")), [])
        if game_bets:
            best = max(game_bets, key=lambda b: num(b.get("edge"), 4) or 0)
            edge = num(best.get("edge"), 4) or 0
            more = f" +{len(game_bets) - 1}" if len(game_bets) > 1 else ""
            row["bet"] = {"v": f"★ {_bet_label(best, away, home)} ({edge:+.1%}){more}", "sort": edge}
        row["flagged"] = bool(game_bets)
        rows.append(row)

    columns = [{"key": "start", "label": "Time (ET)", "sortable": True}] + scoreboard_columns() + [
        {"key": "homeWin", "label": "Home Win", "align": "right", "format": "percent", "sortable": True},
        {"key": "fair", "label": "Fair ML", "align": "right"},
        {"key": "spread", "label": "Proj. Spread", "align": "right", "sortable": True},
        {"key": "total", "label": "Proj. Total", "align": "right", "sortable": True},
    ]
    if has_market:
        columns += [
            {"key": "mktHome", "label": "Mkt Home", "align": "right", "format": "percent", "sortable": True},
            {"key": "ats", "label": "vs Spread", "align": "right", "sortable": True},
            {"key": "ou", "label": "vs Total", "align": "right", "sortable": True},
        ]
    any_bets = any(r["flagged"] for r in rows)
    if any_bets:
        columns.append({"key": "bet", "label": "Bet (edge)", "align": "right", "sortable": True})

    preseason = (df.get("game_type") == "pre").any()
    notes = ["times ET", "score = expected points", "fair ML is the home side's"]
    if has_market:
        notes.append("vs spread / total = the side of the market's line the model favours")
    if any_bets:
        notes.append("★ = flagged bet")
    if preseason:
        notes.append("preseason games are rehearsals: never bet or graded")
    return {
        "subtitle": f"{_day_label(day)}, {_season_label(season)} — {len(rows)} game{'s' * (len(rows) != 1)} · "
                    + " · ".join(notes),
        "updated": updated,
        "search": SEARCH,
        **({"filters": [{**FLAGGED_FILTER, "label": "★ bets only"}]} if any_bets else {}),
        "columns": columns,
        "rows": rows,
    }


# --------------------------------------------------------------------------
# season and rankings
# --------------------------------------------------------------------------
def newest_sim(outputs: Path, season: int) -> Path | None:
    """The season's latest simulation file that has today's columns."""
    found = sorted((m.group("day"), p) for p in (outputs / "sims").glob(f"season_{season}_*.csv")
                   if (m := SIM.match(p.name)))
    for _, path in reversed(found):
        if SIM_COLUMNS <= set(pd.read_csv(path, nrows=0).columns):
            return path
    return None


def build_season(path: Path, season: int) -> dict:
    # Grouped by conference on the site, most projected wins first.
    df = pd.read_csv(path).sort_values(["conference", "wins_mean"], ascending=[True, False])
    rows = []
    for d in df.to_dict("records"):
        wins, games = num(d.get("wins_mean"), 2), int(d.get("games") or 82)
        p10, p90 = num(d.get("wins_p10"), 0), num(d.get("wins_p90"), 0)
        w = round(wins or 0)
        rows.append({
            "team": text(d.get("name")),
            "conference": CONFERENCES.get(text(d.get("conference")), text(d.get("conference"))),
            "record": f"{int(d.get('wins_now') or 0)}–{int(d.get('losses_now') or 0)}",
            "projRecord": {"v": f"{w}–{games - w}", "sort": wins},
            "wins": num(wins, 1),
            "range": None if p10 is None or p90 is None else f"{p10:.0f}–{p90:.0f}",
            "seed": num(d.get("seed_mean"), 1),
            "top6": num(d.get("p_top_6"), 4),
            "playIn": num(d.get("p_play_in"), 4),
            "playoffs": num(d.get("p_playoffs"), 4),
            "confFinals": num(d.get("p_conf_finals"), 4),
            "finals": num(d.get("p_finals"), 4),
            "title": num(d.get("p_champion"), 4),
        })

    columns = [
        {"key": "team", "label": "Team"},
        {"key": "record", "label": "W–L", "align": "right"},
        {"key": "projRecord", "label": "Proj. Record", "align": "right", "sortable": True},
        {"key": "wins", "label": "Proj. Wins", "align": "right", "format": "number", "sortable": True},
        {"key": "range", "label": "Wins 10–90%", "align": "right"},
        {"key": "seed", "label": "Avg Seed", "align": "right", "format": "number", "sortable": True},
        {"key": "top6", "label": "Top 6", "align": "right", "format": "percent", "sortable": True},
        {"key": "playIn", "label": "Play-in", "align": "right", "format": "percent", "sortable": True},
        {"key": "playoffs", "label": "Playoffs", "align": "right", "format": "percent", "sortable": True},
        {"key": "confFinals", "label": "Conf. Finals", "align": "right", "format": "percent", "sortable": True},
        {"key": "finals", "label": "Finals", "align": "right", "format": "percent", "sortable": True},
        {"key": "title", "label": "Title", "align": "right", "format": "percent", "sortable": True},
    ]
    as_of = SIM.match(path.name).group("day")
    return {
        "subtitle": f"{_season_label(season)} projected standings as of {_day_label(as_of)} — season "
                    "simulation: win totals, seeds, play-in and playoff odds (top 6 go straight to the "
                    "playoffs; 7–10 play in)",
        "updated": file_date(path),
        "groupBy": "conference",
        "search": "Search teams",
        "columns": columns,
        "rows": rows,
    }


def _trend_days(model: Path) -> int | None:
    """power_rankings.trend_days from the model's config: the trend's window."""
    try:
        import yaml  # in the model's venv; only for this label
        return int(yaml.safe_load((model / "config" / "nba.yaml").read_text())["power_rankings"]["trend_days"])
    except Exception:
        return None


def build_rankings(path: Path, season: int, trend_days: int | None) -> dict:
    df = pd.read_csv(path).sort_values("rank")
    scores = [("overall", "Overall"), ("offense", "Offense"), ("defense", "Defense"),
              ("shooting", "Shooting"), ("turnovers", "Turnovers"), ("rebounding", "Rebounding"),
              ("free_throws", "Free Throws")]
    rows = []
    for d in df.to_dict("records"):
        trend, net = num(d.get("trend"), 2), num(d.get("net"), 2)
        r = 0 if trend is None else round(trend)
        rows.append({
            "rank": int(d["rank"]),
            "team": text(d.get("name")),
            "record": f"{int(d.get('wins') or 0)}–{int(d.get('losses') or 0)}",
            "net": None if net is None else {"v": f"{net:+.1f}", "sort": net},
            "winPct": num(d.get("p_win"), 4),
            **{key: num(d.get(f"{key}_score"), 0) for key, _ in scores},
            "trend": None if trend is None else {
                "v": f"{r:+d}" if r else "0", "sort": trend,
                **({"tone": "good" if r > 0 else "bad"} if r else {})},
        })

    columns = [
        {"key": "rank", "label": "#", "align": "right", "sortable": True},
        {"key": "team", "label": "Team"},
        {"key": "record", "label": "W–L", "align": "right"},
        {"key": "overall", "label": "Overall", "align": "right", "format": "number", "sortable": True},
        {"key": "net", "label": "Net /100", "align": "right", "sortable": True},
        {"key": "winPct", "label": "Win % vs avg", "align": "right", "format": "percent", "sortable": True},
        *({"key": key, "label": label, "align": "right", "format": "number", "sortable": True}
          for key, label in scores[1:]),
        {"key": "trend", "label": "Trend", "align": "right", "sortable": True},
    ]

    window = f"the last {trend_days} days" if trend_days else "recent days"
    return {
        "subtitle": f"{_season_label(season)} — scores 0–100 are percentiles among the team-seasons of "
                    "earlier seasons (50 = average). Overall = the model's net rating (offense + "
                    "defense, points per 100 possessions vs an average team), the one its predictions "
                    "use; shooting, turnovers, rebounding and free throws are the Four Factors, for "
                    f"context. Trend = change in overall over {window}.",
        "updated": file_date(path),
        "columns": columns,
        "rows": rows,
    }


# --------------------------------------------------------------------------
# results: the published daily predictions graded against final scores
# --------------------------------------------------------------------------
# Graded the way the model grades itself (core/eval/metrics.py): straight up is the home side
# at 50% or more, else the away side; log loss (clipped at 1e-12) and Brier on the win
# probability; margin and total MAE of the expected scores against the final (OT included).
# Against the market's line the published spread and total are graded as leans (the side the
# model gives over 50%), and settled bets are added once there are any. Preseason is left out.
COIN_FLIP = {"log_loss": math.log(2), "brier": 0.25}
BET_RESULT = {"win": "W", "loss": "L", "push": "P"}


def read_db(model: Path, season: int, version: str | None) -> tuple[dict, dict, str | None]:
    """From the model's database (opened read-only): game_id -> (away pts, home pts, OT periods)
    for the season's finished games, game_id -> this version's settled bets, and the season's
    opening night."""
    path = model / "data" / "nba.sqlite"
    if not path.exists():
        return {}, {}, None
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        finals = {r["game_id"]: (r["away_pts"], r["home_pts"], r["ot_periods"]) for r in con.execute(
            "SELECT game_id, away_pts, home_pts, ot_periods FROM games "
            "WHERE season = ? AND home_pts IS NOT NULL AND away_pts IS NOT NULL", (season,))}
        bets: dict[str, list[dict]] = {}
        for r in con.execute(
                "SELECT p.game_id, b.market, b.side, b.line, b.price, b.stake, b.result, b.clv FROM bets b "
                "JOIN predictions p ON p.pred_id = b.pred_id WHERE p.model_version = ? AND b.result IS NOT NULL",
                (version,)):
            bets.setdefault(r["game_id"], []).append(dict(r))
        (opening,) = con.execute("SELECT min(date) FROM games WHERE season = ? AND game_type = 'reg'",
                                 (season,)).fetchone()
    finally:
        con.close()
    return finals, bets, opening


def _graded(lean, won: bool | None) -> tuple[str, str] | None:
    if lean is None:
        return None
    return f"{lean[0]} {lean[1]:.0%}", "P" if won is None else ("W" if won else "L")


def graded_games(frames: dict[str, pd.DataFrame], days: list[str], finals: dict, bets: dict) -> list[dict]:
    """Every published prediction of a regular-season (or later) game that has finished, graded."""
    games = []
    for day in days:
        for d in frames[day].to_dict("records"):
            p = num(d.get("p_home_win"), 6)
            final = finals.get(text(d.get("game_id")))
            if p is None or final is None or text(d.get("game_type")) == "pre":
                continue  # no prediction, not played yet, or a preseason rehearsal
            away_pts, home_pts, ot = final
            away, home = text(d.get("away")), text(d.get("home"))
            am, hm = num(d.get("away_mean"), 4), num(d.get("home_mean"), 4)
            home_won, y = home_pts > away_pts, float(home_pts > away_pts)
            pick = home if p >= 0.5 else away  # the model's own rule: a 50% call is the home side
            q = min(max(p, 1e-12), 1 - 1e-12)
            line, total_line = num(d.get("spread_line")), num(d.get("total_line"))
            ats = _ats(d, away, home)
            cover = None if line is None or home_pts - away_pts + line == 0 else home_pts - away_pts + line > 0
            ou = _ou(d)
            over = None if total_line is None or home_pts + away_pts == total_line else home_pts + away_pts > total_line
            games.append({
                "day": day,
                "order": text(d.get("start_ts")) or "",
                "away": away, "home": home,
                "away_pts": away_pts, "home_pts": home_pts,
                "ot": int(ot or 0),
                "proj_away": am, "proj_home": hm,
                "pick": pick,
                "pick_p": max(p, 1 - p),
                "pick_res": "W" if (pick == home) == home_won else "L",
                "accuracy": float((pick == home) == home_won),
                "log_loss": -(y * math.log(q) + (1 - y) * math.log(1 - q)),
                "brier": (p - y) ** 2,
                "margin_err": None if am is None or hm is None else (hm - am) - (home_pts - away_pts),
                "total_err": None if am is None or hm is None else (hm + am) - (home_pts + away_pts),
                "ats": _graded(ats, None if cover is None or ats is None else cover == ats[0].startswith(home + " ")),
                "ou": _graded(ou, None if over is None or ou is None else over == ou[0].startswith("Over")),
                "bets": [(b, BET_RESULT.get(text(b.get("result")))) for b in bets.get(text(d.get("game_id")), [])],
            })
    return games


def _tally(results) -> tuple[int, int, int]:
    results = list(results)
    return results.count("W"), results.count("L"), results.count("P")


def _rec(t) -> str:
    w, l, p = t
    return f"{w}–{l}" + (f"–{p}" if p else "")


def _profit(bet: dict, result: str | None) -> float:
    """A settled bet's profit, in the stake's units, at its American price."""
    stake, price = num(bet.get("stake"), 6) or 0.0, num(bet.get("price"), 0)
    if result == "W" and price:
        return stake * (price / 100 if price > 0 else 100 / -price)
    return -stake if result == "L" else 0.0


def _mae(errors) -> float | None:
    errors = [abs(e) for e in errors if e is not None]
    return sum(errors) / len(errors) if errors else None


def _numbers(games: list[dict]) -> dict:
    """The model's own metrics, plus records against the market and of the bets, for a set of games."""
    n = len(games)
    settled = [(b, res) for g in games for b, res in g["bets"] if res]
    staked = sum(num(b.get("stake"), 6) or 0.0 for b, _ in settled)
    clvs = [c for b, _ in settled if (c := num(b.get("clv"), 6)) is not None]
    return {
        "n": n,
        "su": _tally(g["pick_res"] for g in games),
        "accuracy": sum(g["accuracy"] for g in games) / n,
        "log_loss": sum(g["log_loss"] for g in games) / n,
        "brier": sum(g["brier"] for g in games) / n,
        "margin_mae": _mae(g["margin_err"] for g in games),
        "total_mae": _mae(g["total_err"] for g in games),
        "ats": _tally(g["ats"][1] for g in games if g["ats"]),
        "ou": _tally(g["ou"][1] for g in games if g["ou"]),
        "bets": _tally(res for _, res in settled),
        "roi": sum(_profit(b, res) for b, res in settled) / staked if staked else None,
        "clv": sum(clvs) / len(clvs) if clvs else None,
    }


def _vs_half(t) -> dict | None:
    """'5–3 (63%)' toned against picking at random; None when nothing was picked."""
    w, l, _ = t
    if w + l == 0:
        return None
    rate = w / (w + l)
    cell = {"v": f"{_rec(t)} ({rate:.0%})", "sort": round(rate, 4)}
    if rate != 0.5:
        cell["tone"] = "good" if rate > 0.5 else "bad"
    return cell


def _lower_better(value: float, metric: str) -> dict:
    """A log loss / Brier score toned against a coin flip (lower is better)."""
    return {"v": f"{value:.3f}", "sort": round(value, 6), "tone": "good" if value < COIN_FLIP[metric] else "bad"}


def _backtest(model: Path, version: str | None) -> dict | None:
    """The live version's walk-forward backtest - the 'model' row of
    outputs/reports/backtest_<version>.md: what live results should look like."""
    try:
        report = (model / "outputs" / "reports" / f"backtest_{version}.md").read_text(encoding="utf-8")
    except (OSError, TypeError):
        return None
    m = re.search(r"^\| model \| ([\d,]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| [\d.]+ \| ([\d.]+) \| ([\d.]+) \|",
                  report, re.M)
    return None if not m else {"games": int(m[1].replace(",", "")), "log_loss": float(m[2]), "brier": float(m[3]),
                               "accuracy": float(m[4]), "margin_mae": float(m[5]), "total_mae": float(m[6])}


def publish_results(view_dir: Path, games: list[dict], *, season: int, updated: str,
                    backtest: dict | None) -> dict:
    """Write the NBA results view - season-to-date tiles, an 'All days' table, one slate per
    day - and return the season's numbers (for the card)."""
    games = sorted(games, key=lambda g: (g["day"], g["order"]))
    total = _numbers(games)
    acc = total["accuracy"]
    stats = [
        {"label": "Straight up", "value": _rec(total["su"]), "detail": f"{acc:.1%}",
         **({"tone": "good" if acc > 0.5 else "bad"} if acc != 0.5 else {})},
        {"label": "Log loss", "value": f"{total['log_loss']:.3f}", "detail": "coin flip 0.693",
         "tone": "good" if total["log_loss"] < COIN_FLIP["log_loss"] else "bad"},
        {"label": "Brier score", "value": f"{total['brier']:.3f}", "detail": "coin flip 0.250",
         "tone": "good" if total["brier"] < COIN_FLIP["brier"] else "bad"},
        {"label": "Margin MAE", "value": "—" if total["margin_mae"] is None else f"{total['margin_mae']:.1f}",
         "detail": "avg miss, points"},
    ]
    for key, label in (("ats", "vs Spread"), ("ou", "vs Total")):
        w, l, _ = total[key]
        if w + l:
            rate = w / (w + l)
            stats.append({"label": label, "value": _rec(total[key]), "detail": f"{rate:.1%}",
                          **({"tone": "good" if rate > 0.5 else "bad"} if rate != 0.5 else {})})
    if sum(total["bets"]):
        roi, clv = total["roi"], total["clv"]
        stats.append({"label": "★ Bets", "value": _rec(total["bets"]),
                      "detail": " · ".join(x for x in (
                          None if roi is None else f"ROI {roi:+.1%}",
                          None if clv is None else f"CLV {clv:+.1%}") if x) or "—",
                      **({"tone": "good" if roi > 0 else "bad"} if roi else {})})
    bt = "" if not backtest else (
        f" Walk-forward backtest of this model version ({backtest['games']:,} games): "
        f"{backtest['accuracy']:.1%} straight up, log loss {backtest['log_loss']:.3f}, Brier "
        f"{backtest['brier']:.3f}, margin MAE {backtest['margin_mae']:.1f}, total MAE {backtest['total_mae']:.1f}.")
    summary = {
        "title": f"{_season_label(season)} season to date · {total['n']} games graded",
        "stats": stats,
        "note": "Graded the way the model grades itself: straight up is the home side at 50% or more, "
                "else the away side; log loss and Brier score the win probability (lower is better); "
                "margin and total MAE compare the expected scores with the final (overtime included). "
                "Against the market, the published spread and total are graded as the side the model "
                "gives over 50%. Preseason games are left out." + bt,
    }

    any_bets = any(g["bets"] for g in games)
    game_cols = scoreboard_columns("Final") + [
        {"key": "proj", "label": "Proj.", "align": "right"},
        {"key": "pick", "label": "Win Pick", "align": "right", "sortable": True},
        {"key": "margin", "label": "Margin: proj → final", "align": "right", "sortable": True},
        {"key": "total", "label": "Total: proj → final", "align": "right", "sortable": True},
        {"key": "ats", "label": "vs Spread", "align": "right", "sortable": True},
        {"key": "ou", "label": "vs Total", "align": "right", "sortable": True},
        *([{"key": "bet", "label": "Bet", "align": "right", "sortable": True}] if any_bets else []),
    ]
    slates, by_day = [], []
    for day, day_games in groupby(games, key=lambda g: g["day"]):
        day_games = list(day_games)
        r = _numbers(day_games)
        rows = []
        for g in day_games:
            pa, ph = g["proj_away"], g["proj_home"]
            bet_cells = [graded_cell(f"★ {_bet_label(b, g['away'], g['home'])}", res) for b, res in g["bets"]]
            rows.append({
                **scoreboard(g["away"], g["home"], g["away_pts"], g["home_pts"], digits=0),
                "sep": "–" if not g["ot"] else ("OT" if g["ot"] == 1 else f"{g['ot']}OT"),
                "proj": None if pa is None or ph is None else f"{pa:.1f} – {ph:.1f}",
                "pick": graded_cell(f"{g['pick']} {g['pick_p']:.0%}", g["pick_res"]),
                "margin": None if g["margin_err"] is None else {
                    "v": f"{ph - pa:+.1f} → {g['home_pts'] - g['away_pts']:+d}", "sort": round(abs(g["margin_err"]), 4)},
                "total": None if g["total_err"] is None else {
                    "v": f"{pa + ph:.1f} → {g['home_pts'] + g['away_pts']}", "sort": round(abs(g["total_err"]), 4)},
                **{key: graded_cell(*g[key]) for key in ("ats", "ou") if g[key]},
                **({"bet": bet_cells[0]} if bet_cells else {}),
                "flagged": bool(g["bets"]),
            })
        line = [f"{r['n']} game{'s' * (r['n'] != 1)}", f"straight up {_rec(r['su'])}", f"log loss {r['log_loss']:.3f}"]
        if r["margin_mae"] is not None:
            line.append(f"margin MAE {r['margin_mae']:.1f}")
        slates.append(Slate(day, _day_label(day), {
            "subtitle": f"{_day_label(day)}, {_season_label(season)} — " + " · ".join(line)
                        + " · margins are the home side's",
            "updated": updated,
            "search": "Search teams",
            **({"filters": [{**FLAGGED_FILTER, "label": "★ bets only"}]} if any(row["flagged"] for row in rows) else {}),
            "columns": game_cols,
            "rows": rows,
        }))
        by_day.append({
            "day": {"v": _day_label(day), "sort": day},
            "games": r["n"],
            "su": _vs_half(r["su"]),
            "logLoss": _lower_better(r["log_loss"], "log_loss"),
            "brier": _lower_better(r["brier"], "brier"),
            "mae": None if r["margin_mae"] is None else {"v": f"{r['margin_mae']:.1f}", "sort": round(r["margin_mae"], 4)},
            "ats": _vs_half(r["ats"]),
            "ou": _vs_half(r["ou"]),
            **({"bets": _vs_half(r["bets"])} if any_bets else {}),
        })

    overview = {
        "subtitle": f"{_season_label(season)} · record by day",
        "updated": updated,
        "columns": [
            {"key": "day", "label": "Day", "sortable": True},
            {"key": "games", "label": "Games", "align": "right"},
            {"key": "su", "label": "Straight up", "align": "right", "sortable": True},
            {"key": "logLoss", "label": "Log loss", "align": "right", "sortable": True},
            {"key": "brier", "label": "Brier", "align": "right", "sortable": True},
            {"key": "mae", "label": "Margin MAE", "align": "right", "sortable": True},
            {"key": "ats", "label": "vs Spread", "align": "right", "sortable": True},
            {"key": "ou", "label": "vs Total", "align": "right", "sortable": True},
            *([{"key": "bets", "label": "★ Bets", "align": "right", "sortable": True}] if any_bets else []),
        ],
        "rows": by_day,
    }
    # "All days" first in the dropdown; the view opens on the latest graded day.
    publish_slates(view_dir, [Slate(f"{season}-all", "All days", overview), *slates],
                   season=season, label=f"{_season_label(season)} Season", unit="day",
                   latest=slates[-1].id, summary=summary)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", help="day the site opens on, YYYY-MM-DD (default: latest predicted)")
    ap.add_argument("--model", type=Path, default=MODEL, help="the NBA model's repo")
    ap.add_argument("--site-dir", type=Path, default=SITE_DATA / "nba", help="site public/data/nba dir")
    args = ap.parse_args()
    outputs = args.model / "outputs"
    version = live_version(args.model)

    frames: dict[str, pd.DataFrame] = {}
    paths: dict[str, Path] = {}
    for path in sorted((outputs / "daily").glob("*_predictions.csv")):
        m = DAILY.match(path.name)
        if not m:
            continue  # a rehearsal's <date>_dry_predictions.csv
        df = read_csv_stable(path)
        if version is not None and "model_version" in df:
            df = df[df["model_version"] == version]
        if len(df):
            frames[m.group("day")], paths[m.group("day")] = df, path
    seasons = {day: _season_of(df["game_id"].iloc[0]) for day, df in frames.items()}
    sims = [int(m.group("season")) for p in (outputs / "sims").glob("season_*.csv") if (m := SIM.match(p.name))]
    season = max(seasons.values(), default=max(sims, default=date.today().year))
    publish = sorted(day for day, s in seasons.items() if s == season)
    opens = args.day if args.day in publish else (publish[-1] if publish else None)

    print(f"Exporting NBA {_season_label(season)} (model {version or 'unknown'}"
          f"{f', opens on {opens}' if opens else ', no day predicted yet'})\n  from: {outputs}\n  to:   {args.site_dir}")
    slates = []
    for day in publish:
        bets_path = paths[day].with_name(f"{day}_bets.csv")
        bets = read_csv_stable(bets_path) if bets_path.exists() else pd.DataFrame(columns=["game_id"])
        slates.append(Slate(day, _day_label(day), build_day(frames[day], bets, day, season, file_date(paths[day]))))
    publish_slates(args.site_dir / "games", slates, season=season, label=f"{_season_label(season)} Season",
                   unit="day", latest=opens)
    sources = [paths[day] for day in publish]

    sim = newest_sim(outputs, season)
    title_favorite = None
    if sim is not None:
        write_table(args.site_dir / "season.json", build_season(sim, season))
        sources.append(sim)
        top = pd.read_csv(sim).sort_values("p_champion", ascending=False).iloc[0]
        title_favorite = {"label": "Title favorite", "value": text(top["team"]),
                          "detail": f"{num(top['p_champion'], 4):.1%} title odds"}
    else:
        print(f"  [skip] no {season} season simulation in {outputs / 'sims'}")

    power = outputs / "power_rankings.csv"
    top_power = None
    if power.exists():
        write_table(args.site_dir / "rankings.json", build_rankings(power, season, _trend_days(args.model)))
        sources.append(power)
        best = pd.read_csv(power).sort_values("rank").iloc[0]
        top_power = {"label": "#1 power", "value": text(best["abbrev"]),
                     "detail": f"{num(best['overall_score'], 0):.0f} overall"}
    else:
        print(f"  [skip] no {power.name}")

    finals, bets, opening = read_db(args.model, season, version)
    graded = graded_games(frames, publish, finals, bets)
    record = None
    if graded:
        db = args.model / "data" / "nba.sqlite"
        record = publish_results(args.site_dir / "results", graded, season=season,
                                 updated=file_date(db), backtest=_backtest(args.model, version))
        sources.append(db)
    else:
        publish_slates(args.site_dir / "results", [], season=season, label=f"{_season_label(season)} Season",
                       unit="day", latest=None)

    # The card leads with the model's record once games are graded, else its #1 team.
    straight_up = None if record is None else {
        "label": "Straight up", "value": _rec(record["su"]), "detail": f"{record['accuracy']:.1%}",
        **({"tone": "good" if record["accuracy"] > 0.5 else "bad"} if record["accuracy"] != 0.5 else {})}
    headline = (_day_label(opens) if opens else
                f"Opening night {_day_label(opening)}" if opening else f"{_season_label(season)} season")
    write_card(args.site_dir, headline=headline,
               updated=max((file_date(p) for p in sources), default=None),
               stats=[s for s in (straight_up or top_power, title_favorite) if s])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
