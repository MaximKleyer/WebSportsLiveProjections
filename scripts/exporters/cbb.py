#!/usr/bin/env python
"""College basketball adapter: the CBB model's outputs -> the site's JSON (via site_export).

Reads the model's outputs/ - plus, for final scores and settled bets, its database (read-only) -
and writes public/data/cbb/:

    games/          one slate per DAY           outputs/daily/<date>_predictions.csv
                                                (+ <date>_bets.csv: flagged bets). Every game
                                                twice, Barttorvik alone beside the user's
                                                twist, with a card per game: the twist layer
                                                by layer, who's out, the market, the bets. A
                                                coming day without one yet: outputs/early/
                                                <date>.csv, the early look (`cbb early`:
                                                nothing stored, no bets), until the gameday
                                                task's run
    results/        those predictions graded,   final scores + settled bets from data/cbb.sqlite
                    one slate per day           (read-only); the season's records per variant
                                                and per twist layer from outputs/records.json
    season.json     the season outlook: the     the newest outputs/sims/season_<season>_<date>.csv (`cbb season`),
                    regular season, conference  and once the NCAA bracket is out, the newest
                    tournaments, NCAA odds      ncaa_<season>_<date>.csv (`cbb ncaa`)
    rankings.json   every D1 team               outputs/rankings.csv (`cbb rankings`)
    card.json       the landing card's line

Rehearsals and replays write <date>_dry_* files, which are never published. The model's "CBB
gameday" scheduled task rewrites a day's files while games are on, so a file caught mid-write
is read again.

scripts/update-data.ps1 runs this with the model's own venv Python (pandas):

    <model>\\.venv\\Scripts\\python.exe scripts\\exporters\\cbb.py [--day 2026-11-02]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from datetime import date, datetime
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_export import (  # noqa: E402  (the site's shared export toolkit)
    FLAGGED_FILTER, SITE_DATA, Slate, file_date, final_score, graded_cell, num, publish_slates, scoreboard,
    scoreboard_columns, spread, text, versus_row, write_card, write_table,
)

MODEL = Path(r"C:\Users\maxim\WebSportsLiveProjections\models\cbb")
EASTERN = ZoneInfo("America/New_York")
DAILY = re.compile(r"(?P<day>\d{4}-\d{2}-\d{2})_predictions\.csv$")  # a rehearsal's <date>_dry_* never matches
EARLY = re.compile(r"(?P<day>\d{4}-\d{2}-\d{2})\.csv$")  # outputs/early/: the early look
GAME_TYPES = {"conf_tourney": "conf. tournament", "ncaa": "NCAA tournament", "nit": "NIT", "post": "postseason"}
LAYERS = [("bpi", "ESPN BPI (second guide)"), ("form", "Form (this season's games)"), ("tilt", "Offense/defense tilt"), ("tournament_pace", "Tournament pace"),
          ("four_factors", "Four factors matchup"), ("star_boost", "Star boost"), ("availability", "Who's out")]
SEARCH = "Search teams or players"
SIM = re.compile(r"season_(?P<season>\d{4})_(?P<day>\d{4}-\d{2}-\d{2})\.csv$")
NCAA = re.compile(r"ncaa_(?P<season>\d{4})_(?P<day>\d{4}-\d{2}-\d{2})\.csv$")
CONFERENCES = {  # Barttorvik's codes, as the model keeps them
    "A10": "Atlantic 10", "ACC": "ACC", "AE": "America East", "ASun": "ASUN", "Amer": "American", "B10": "Big Ten",
    "B12": "Big 12", "BE": "Big East", "BSky": "Big Sky", "BSth": "Big South", "BW": "Big West", "CAA": "CAA",
    "CUSA": "Conference USA", "Horz": "Horizon", "Ivy": "Ivy League", "MAC": "MAC", "MEAC": "MEAC", "MVC": "Missouri Valley",
    "MWC": "Mountain West", "Metro": "MAAC", "NEC": "Northeast", "OVC": "Ohio Valley", "P12": "Pac-12", "Pat": "Patriot",
    "SB": "Sun Belt", "SC": "Southern", "SEC": "SEC", "SWAC": "SWAC", "Slnd": "Southland", "Sum": "Summit",
    "UAC": "United Athletic", "WCC": "West Coast", "WAC": "WAC",
}
COIN_FLIP = {"log_loss": math.log(2), "brier": 0.25}
BET_RESULT = {"win": "W", "loss": "L", "push": "P"}


def _season_label(season: int) -> str:
    """2026 -> '2026–27' (the model's season id is the year it starts)."""
    return f"{season}–{(season + 1) % 100:02d}"


def _day_label(day: str) -> str:
    """'2026-11-02' -> 'Mon Nov 2'."""
    d = date.fromisoformat(day)
    return f"{d:%a} {d:%b} {d.day}"


def _season_of(day: str) -> int:
    """A game date's season: one that starts in the autumn runs into spring."""
    d = date.fromisoformat(day)
    return d.year if d.month >= 8 else d.year - 1


def _start(d: dict) -> dict | None:
    """A cell showing the tip-off ('7:00 PM' ET, or 'TBD' while ESPN hasn't set it) that sorts by
    time, tagged with a postseason game's kind and a neutral site."""
    ts = text(d.get("start_ts"))
    if ts is None:
        return None
    tags = [t for t in (GAME_TYPES.get(text(d.get("game_type")) or ""), "neutral" if _flag(d.get("neutral")) else None) if t]
    if not _flag(d.get("time_valid"), default=True):
        return {"v": "TBD", "sort": "9" + ts, **({"sub": " · ".join(tags)} if tags else {})}
    et = pd.Timestamp(ts).tz_convert(EASTERN)
    return {"v": f"{et.hour % 12 or 12}:{et.minute:02d} {'AM' if et.hour < 12 else 'PM'}", "sort": ts,
            **({"sub": " · ".join(tags)} if tags else {})}


def _flag(x, default: bool = False) -> bool:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return default
    if isinstance(x, str):
        return x.strip().lower() in ("true", "1")
    return bool(x)


def _line(home: str, away: str, margin: float | None) -> str | None:
    """The favourite and margin from a home margin: 'Duke -7.5', or 'PK'."""
    if margin is None:
        return None
    return "PK" if abs(margin) < 0.05 else f"{home if margin > 0 else away} -{abs(margin):.1f}"


def _out(players, show: int = 2) -> str | None:
    """Who's listed or presumed out for a team, cut to the first `show` (costliest first)."""
    names = [p.strip() for p in (text(players) or "").split(",") if p.strip()]
    if not names:
        return None
    return ", ".join(names[:show]) + (f" +{len(names) - show}" if len(names) > show else "")


def _lean(p, line, yes: str, no: str) -> tuple[str, float] | None:
    """The side of a market line the twist favours, with its probability (p: the home side
    covering, the over). None at exactly 50% or without a line."""
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
    """'Duke ML', 'Duke -7.5', 'Over 148.5' - as the model's own reports write a bet."""
    market, side, line = text(bet.get("market")), text(bet.get("side")), num(bet.get("line"))
    if market == "total":
        return f"{(side or '').title()} {'' if line is None else f'{line:g}'}".strip()
    team = home if side == "home" else away
    return f"{team} ML" if market == "ml" or line is None else f"{team} {line:+g}"


def read_csv_stable(path: Path, tries: int = 5) -> pd.DataFrame:
    """A CSV the gameday task may be rewriting right now: read it until the file stood still
    while it was read and every row is whole."""
    for _ in range(tries):
        before = path.stat()
        try:
            df = pd.read_csv(path, dtype={"game_id": str})
        except (pd.errors.ParserError, pd.errors.EmptyDataError):
            df = None
        after = path.stat()
        if df is not None and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return df
        time.sleep(1)
    raise SystemExit(f"{path.name} kept changing while it was read - try again in a minute")


# --------------------------------------------------------------------------
# games: one slate per day, a card per game
# --------------------------------------------------------------------------
def _early_note(df: pd.DataFrame) -> str:
    """What an early look is, as of its run: 'early look as of 2:10 PM ET Tue: ...'."""
    at = pd.to_datetime(df["as_of"], utc=True).max().tz_convert(EASTERN)
    return (f"EARLY LOOK as of {at.hour % 12 or 12}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'} ET {at:%a}: before"
            " game-day news (Barttorvik's morning file, who's out) and without bets; the gameday task's run replaces it"
            " 75 minutes before the first tip")


def build_day(df: pd.DataFrame, bets: pd.DataFrame, day: str, season: int, updated: str, early: bool = False) -> dict:
    bets_by_game: dict = {}
    for b in bets.to_dict("records"):
        bets_by_game.setdefault(text(b.get("game_id")), []).append(b)
    has_market = any(df[c].notna().any() for c in ("spread_line", "total_line") if c in df)
    has_torvik = "torvik_home" in df and df["torvik_home"].notna().any()

    rows = []
    for d in df.to_dict("records"):
        away, home = text(d.get("away")), text(d.get("home"))
        am, hm = num(d.get("away_mean"), 2), num(d.get("home_mean"), 2)
        ta, th = num(d.get("torvik_away"), 2), num(d.get("torvik_home"), 2)
        p_home = num(d.get("p_home_win"), 4)
        margin = None if am is None or hm is None else round(hm - am, 2)
        t_margin = None if ta is None or th is None else round(th - ta, 2)
        row = {
            "start": _start(d),
            **scoreboard(away, home, am, hm),
            "homeWin": p_home,
            "spread": None if margin is None else {"v": _line(home, away, margin), "sort": margin},
            "total": None if margin is None else {"v": f"{am + hm:.1f}", "sort": round(am + hm, 2)},
        }
        if has_torvik:
            row["torvik"] = None if t_margin is None else {"v": _line(home, away, t_margin), "sort": t_margin}
            move = None if t_margin is None or margin is None else round(margin - t_margin, 2)
            row["move"] = None if move is None else {"v": f"{move:+.1f}", "sort": move,
                                                     **({"tone": "muted"} if abs(move) < 0.5 else {})}
        for key, side in (("awayTeam", "away"), ("homeTeam", "home")):  # who's out, under the team
            out = _out(d.get(f"{side}_out"))
            if out and row[key]:
                row[key] = {"v": row[key], "sub": out}
        if has_market:
            for key, lean in (("ats", _ats(d, away, home)), ("ou", _ou(d))):
                row[key] = None if lean is None else {"v": f"{lean[0]} · {lean[1]:.0%}", "sort": round(lean[1], 4)}
        row["id"] = text(d.get("game_id"))  # opens the game's card
        game_bets = bets_by_game.get(row["id"], [])
        if game_bets:
            best = max(game_bets, key=lambda b: num(b.get("edge"), 4) or 0)
            edge = num(best.get("edge"), 4) or 0
            more = f" +{len(game_bets) - 1}" if len(game_bets) > 1 else ""
            row["bet"] = {"v": f"★ {_bet_label(best, away, home)} ({edge:+.1%}){more}", "sort": edge}
        row["flagged"] = bool(game_bets)
        rows.append(row)

    columns = [{"key": "start", "label": "Time (ET)", "sortable": True}] + scoreboard_columns() + [
        {"key": "homeWin", "label": "Home Win", "align": "right", "format": "percent", "sortable": True}]
    if has_torvik:
        columns.append({"key": "torvik", "label": "Barttorvik", "align": "right", "sortable": True})
    columns += [{"key": "spread", "label": "Twist", "align": "right", "sortable": True}]
    if has_torvik:
        columns.append({"key": "move", "label": "Twist Δ", "align": "right", "sortable": True})
    columns.append({"key": "total", "label": "Proj. Total", "align": "right", "sortable": True})
    if has_market:
        columns += [{"key": "ats", "label": "vs Spread", "align": "right", "sortable": True},
                    {"key": "ou", "label": "vs Total", "align": "right", "sortable": True}]
    any_bets = any(r["flagged"] for r in rows)
    if any_bets:
        columns.append({"key": "bet", "label": "Bet (edge)", "align": "right", "sortable": True})

    notes = ([_early_note(df)] if early else []) + ["times ET", "score = expected points with the twist"]
    if has_torvik:
        notes.append("Barttorvik = his ratings alone through the model; Twist Δ = how far the twist moves the home margin")
    if has_market:
        notes.append("vs spread / total = the side of the market's line the twist favours")
    if any_bets:
        notes.append("★ = flagged bet")
    return {
        "subtitle": f"{_day_label(day)}, {_season_label(season)} — {len(rows)} game{'s' * (len(rows) != 1)} · "
                    + " · ".join(notes) + " · open a game for its layers and who's out",
        "updated": updated,
        "search": SEARCH,
        **({"filters": [{**FLAGGED_FILTER, "label": "★ bets only"}]} if any_bets else {}),
        "columns": columns,
        "rows": rows,
    }


def _pct(p) -> str:
    return "-" if p is None else f"{p:.0%}"


def game_card(d: dict, game_bets: list[dict], day: str) -> dict:
    away, home = text(d.get("away")), text(d.get("home"))
    am, hm = num(d.get("away_mean"), 2), num(d.get("home_mean"), 2)
    ta, th = num(d.get("torvik_away"), 2), num(d.get("torvik_home"), 2)
    p_home, t_home = num(d.get("p_home_win"), 4), num(d.get("torvik_p_home_win"), 4)
    neutral, start = _flag(d.get("neutral")), _start(d)
    subtitle = " · ".join(x for x in (_day_label(day), None if start is None else f"{start['v']} ET",
                                      "neutral site" if neutral else None, GAME_TYPES.get(text(d.get("game_type")) or "")) if x)
    sections = []
    variants = [] if am is None or hm is None else [["With the twist", _line(home, away, hm - am) or "-", f"{am + hm:.1f}", _pct(p_home)]]
    if ta is not None and th is not None:
        variants.insert(0, ["Barttorvik alone", _line(home, away, th - ta) or "-", f"{ta + th:.1f}", _pct(t_home)])
    if variants:
        sections.append({"title": "Barttorvik alone vs with the twist", "kind": "table",
                         "columns": ["", "Line", "Total", f"{home} win"], "rows": variants,
                         "note": "Barttorvik's ratings through the model's game model, then the user's twist on top"})
    moved = [(label, num(d.get(f"{key}_pts"), 2)) for key, label in LAYERS]
    moved = [(label, pts) for label, pts in moved if pts is not None and abs(pts) >= 0.05]
    if moved:
        total = sum(pts for _, pts in moved)
        sections.append({"title": "The twist, layer by layer", "kind": "table",
                         "columns": ["Layer", f"Points to {home}"],
                         "rows": [[label, f"{pts:+.1f}"] for label, pts in moved] + [["All layers", f"{total:+.1f}"]],
                         "note": "each layer's points on the home margin, on top of the ones before it"})
    else:
        sections.append({"title": "The twist, layer by layer", "kind": "notes",
                         "items": ["No layer moves this game yet: form and four factors start once both teams have played 5 games"]})
    out = [f"{team}: {text(d.get(f'{side}_out'))}" for team, side in ((away, "away"), (home, "home")) if text(d.get(f"{side}_out"))]
    if out:
        sections.append({"title": "Who's out", "kind": "notes", "items": out,
                         "note": "your availability list, then players who missed their team's last game (box score)"})
    market = []
    line, total_line = num(d.get("spread_line")), num(d.get("total_line"))
    if line is not None:
        cover = num(d.get("p_home_cover"), 4)
        fav, fav_p = (home, cover) if line <= 0 else (away, None if cover is None else 1 - cover)
        market.append(f"Spread: {fav} {spread(-abs(line))}" + ("" if fav_p is None else f" · the twist gives {fav} {fav_p:.0%} to cover"))
    if total_line is not None:
        over = num(d.get("p_over"), 4)
        market.append(f"Total: {total_line:g}" + ("" if over is None else f" · over {over:.0%}, under {1 - over:.0%}"))
    if market:
        sections.append({"title": "The market", "kind": "notes", "items": market,
                         "note": "the book's line known when the game was predicted (pushes left out)"})
    if game_bets:
        sections.append({"title": "Bets", "kind": "table", "columns": ["Bet", "Price", "Model", "Market", "Edge", "Stake"],
                         "rows": [[_bet_label(b, away, home), f"{int(num(b.get('price'), 0)):+d}", _pct(num(b.get("model_p"), 4)),
                                   _pct(num(b.get("market_p"), 4)), f"{num(b.get('edge'), 4):+.1%}", f"{num(b.get('stake'), 2):.2f}"]
                                  for b in game_bets]})
    card = {"title": f"{away} {'vs.' if neutral else 'at'} {home}", "subtitle": subtitle,
            "away": {"name": away, **({"note": _out(d.get("away_out"), 3)} if text(d.get("away_out")) else {})},
            "home": {"name": home, **({"note": _out(d.get("home_out"), 3)} if text(d.get("home_out")) else {})},
            "sections": sections}
    if am is not None and hm is not None:
        card["score"] = {"away": f"{am:.1f}", "home": f"{hm:.1f}", "label": "Expected, with the twist"}
    if p_home is not None:
        card["win"] = {"away": round(1 - p_home, 4), "home": p_home}
    return card


def build_cards(df: pd.DataFrame, bets: pd.DataFrame, day: str) -> dict[str, dict]:
    by_game: dict = {}
    for b in bets.to_dict("records"):
        by_game.setdefault(text(b.get("game_id")), []).append(b)
    return {text(d["game_id"]): game_card(d, by_game.get(text(d["game_id"]), []), day) for d in df.to_dict("records")}


# --------------------------------------------------------------------------
# season: the regular-season outlook
# --------------------------------------------------------------------------
def newest_sim(outputs: Path, season: int, kind: str = "season") -> Path | None:
    """The newest `cbb season` (or, kind "ncaa", `cbb ncaa`) file of the season."""
    pattern = SIM if kind == "season" else NCAA
    found = sorted((m.group("day"), p) for p in (outputs / "sims").glob(f"{kind}_{season}_*.csv") if (m := pattern.match(p.name)))
    return found[-1][1] if found else None


def _projected(wins, games) -> dict | None:
    """A projected record to the tenth that adds up to the games ('24.8–7.2'), sorted by wins."""
    w = num(wins, 4)
    if w is None:
        return None
    shown = round(w, 1)
    return {"v": f"{shown:.1f}–{int(games or 0) - shown:.1f}", "sort": round(w, 2)}


def build_season(path: Path, season: int, ncaa: Path | None = None) -> dict:
    """The outlook grouped by conference, likeliest champion first (sports/cbb/season.py's table),
    with each team's NCAA tournament odds once the bracket is out (sports/cbb/ncaa.py's)."""
    df = pd.read_csv(path).sort_values(["conference", "finish_mean", "team"])
    bracket = pd.read_csv(ncaa).set_index("team_id") if ncaa is not None else None
    rows = []
    for d in df.to_dict("records"):
        n = bracket.loc[d["team_id"]] if bracket is not None and d["team_id"] in bracket.index else None
        p10, p90 = num(d.get("wins_p10"), 0), num(d.get("wins_p90"), 0)
        rows.append({
            "team": text(d.get("team")),
            "conference": CONFERENCES.get(text(d.get("conference")) or "", text(d.get("conference")) or "Independent"),
            "record": f"{int(d.get('wins_now') or 0)}–{int(d.get('losses_now') or 0)}",
            "projRecord": _projected(d.get("wins_mean"), d.get("games")),
            "range": None if p10 is None or p90 is None else f"{p10:.0f}–{p90:.0f}",
            "confRecord": f"{int(d.get('conf_wins_now') or 0)}–{int(d.get('conf_losses_now') or 0)}",
            "projConf": _projected(d.get("conf_wins_mean"), d.get("conf_games")),
            "title": num(d.get("p_title"), 4),
            "outright": num(d.get("p_outright"), 4),
            "finish": num(d.get("finish_mean"), 1),
            "seed": num(d.get("seed_mean"), 1),
            "tourney": num(d.get("p_tourney"), 4),
            **({} if bracket is None else {
                "ncaa": None if n is None else {"v": f"{int(n['seed'])} {text(n['region'])}", "sort": int(n["seed"])},
                "ncaaF4": None if n is None else num(n["p_f4"], 4),
                "ncaaTitle": None if n is None else num(n["p_champion"], 4)}),
        })
    columns = [
        {"key": "team", "label": "Team"},
        {"key": "record", "label": "W–L", "align": "right"},
        {"key": "projRecord", "label": "Proj. W–L", "align": "right", "sortable": True},
        {"key": "range", "label": "Wins 10–90%", "align": "right"},
        {"key": "confRecord", "label": "Conf", "align": "right"},
        {"key": "projConf", "label": "Proj. Conf", "align": "right", "sortable": True},
        {"key": "title", "label": "Title", "align": "right", "format": "percent", "sortable": True},
        {"key": "outright", "label": "Outright", "align": "right", "format": "percent", "sortable": True},
        {"key": "finish", "label": "Avg Finish", "align": "right", "format": "number", "sortable": True},
        {"key": "seed", "label": "Tourney Seed", "align": "right", "format": "number", "sortable": True},
        {"key": "tourney", "label": "Tourney", "align": "right", "format": "percent", "sortable": True},
    ]
    if bracket is not None:
        columns += [{"key": "ncaa", "label": "NCAA", "align": "right", "sortable": True},
                    {"key": "ncaaF4", "label": "Final Four", "align": "right", "format": "percent", "sortable": True},
                    {"key": "ncaaTitle", "label": "NCAA Title", "align": "right", "format": "percent", "sortable": True}]
    as_of = SIM.match(path.name).group("day")
    ncaa_note = (f" NCAA = the tournament seed and region; Final Four and NCAA Title from {10000:,} simulated tournaments of"
                 f" the bracket as of {_day_label(NCAA.match(ncaa.name).group('day'))}.") if ncaa is not None else ""
    return {
        "subtitle": f"{_season_label(season)} season as of {_day_label(as_of)} — the rest of the regular season and every "
                    "conference tournament simulated 10,000 times from the schedule, each team's rating shifted once per "
                    "season by its uncertainty. Records are D1 regular-season games only (no non-D1 opponents). "
                    "Title = first or tied for first in the conference's regular season; Outright = first alone. "
                    "Tourney Seed = the mean conference tournament seed; Tourney = winning the conference tournament, "
                    "and with it the automatic NCAA bid." + ncaa_note,
        "updated": file_date(path),
        "groupBy": "conference",
        "search": "Search teams",
        "columns": columns,
        "rows": rows,
    }


# --------------------------------------------------------------------------
# rankings
# --------------------------------------------------------------------------
def build_rankings(path: Path, season: int) -> dict:
    df = pd.read_csv(path).sort_values("rank")
    has_form = df["form_net"].notna().any()
    rows = []
    for d in df.to_dict("records"):
        net, t_net, f_net = num(d.get("net"), 2), num(d.get("torvik_net"), 2), num(d.get("form_net"), 2)
        t_rank = num(d.get("torvik_rank"), 0)
        rows.append({
            "rank": int(d["rank"]),
            "team": text(d.get("team")),
            "conf": text(d.get("conference")),
            "record": f"{int(d.get('wins') or 0)}–{int(d.get('losses') or 0)}",
            "net": None if net is None else {"v": f"{net:+.1f}", "sort": net},
            "off": num(d.get("off"), 1),
            "def": num(d.get("def"), 1),
            "tempo": num(d.get("tempo"), 1),
            "torvik": None if t_net is None else {"v": f"{t_net:+.1f}" + ("" if t_rank is None else f" (#{int(t_rank)})"), "sort": t_net},
            **({"form": None if f_net is None else {"v": f"{f_net:+.1f}", "sort": f_net},
                "share": num(d.get("form_share"), 2)} if has_form else {}),
        })
    columns = [
        {"key": "rank", "label": "#", "align": "right", "sortable": True},
        {"key": "team", "label": "Team"},
        {"key": "conf", "label": "Conf", "sortable": True},
        {"key": "record", "label": "D1 W–L", "align": "right"},
        {"key": "net", "label": "Net /100", "align": "right", "sortable": True},
        {"key": "off", "label": "Off", "align": "right", "format": "number", "sortable": True},
        {"key": "def", "label": "Def", "align": "right", "format": "number", "sortable": True},
        {"key": "tempo", "label": "Tempo", "align": "right", "format": "number", "sortable": True},
        {"key": "torvik", "label": "Barttorvik", "align": "right", "sortable": True},
        *([{"key": "form", "label": "Form", "align": "right", "sortable": True},
           {"key": "share", "label": "Form share", "align": "right", "format": "percent", "sortable": True}] if has_form else []),
    ]
    as_of = text(df["as_of"].iloc[0]) if len(df) else None
    return {
        "subtitle": f"{_season_label(season)} — every D1 team by the model's net rating (offense less defense, points per "
                    "100 possessions against an average team, home court left out): Barttorvik's live T-Rank, leaned "
                    "toward this season's form once a team has played 5 games (Form share). Barttorvik = his own net "
                    "rating and rank." + (f" As of {as_of[:10]}." if as_of else ""),
        "updated": file_date(path),
        "search": "Search teams",
        "columns": columns,
        "rows": rows,
    }


# --------------------------------------------------------------------------
# results: the published predictions graded against final scores
# --------------------------------------------------------------------------
def read_db(model: Path, season: int) -> tuple[dict, dict, str | None]:
    """From the model's database (opened read-only): game_id -> (away pts, home pts, OT periods)
    for the season's finished games, game_id -> its settled bets, and the season's first game day."""
    path = model / "data" / "cbb.sqlite"
    if not path.exists():
        return {}, {}, None
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        finals = {r["game_id"]: (r["away_pts"], r["home_pts"], r["ot_periods"]) for r in con.execute(
            "SELECT game_id, away_pts, home_pts, ot_periods FROM games WHERE season = ? AND status = 'final'"
            " AND home_pts IS NOT NULL AND away_pts IS NOT NULL", (season,))}
        bets: dict[str, list[dict]] = {}
        for r in con.execute(
                "SELECT p.game_id, b.market, b.side, b.line, b.price, b.stake, b.result, b.clv FROM bets b"
                " JOIN predictions p ON p.pred_id = b.pred_id JOIN games g ON g.game_id = p.game_id"
                " WHERE g.season = ? AND b.result IS NOT NULL", (season,)):
            bets.setdefault(r["game_id"], []).append(dict(r))
        (opening,) = con.execute("SELECT min(date) FROM games WHERE season = ? AND game_type <> 'pre'", (season,)).fetchone()
    finally:
        con.close()
    return finals, bets, opening


def read_boxes(model: Path, season: int) -> dict[str, dict]:
    """game_id -> its box score from the model's database (read-only): each team's (by ESPN id)
    and each player's line."""
    path = model / "data" / "cbb.sqlite"
    if not path.exists():
        return {}
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    boxes: dict[str, dict] = {}
    try:
        for r in con.execute("SELECT s.* FROM team_game_stats s JOIN games g USING (game_id) WHERE g.season = ?", (season,)):
            boxes.setdefault(r["game_id"], {"teams": {}, "players": []})["teams"][int(r["team_id"])] = dict(r)
        for r in con.execute("SELECT s.*, p.name FROM player_game_stats s JOIN players p USING (player_id) "
                             "JOIN games g USING (game_id) WHERE g.season = ?", (season,)):
            boxes.setdefault(r["game_id"], {"teams": {}, "players": []})["players"].append(dict(r))
    finally:
        con.close()
    return boxes


OUT_ENTRY = re.compile(r"^(?P<name>.+?) (?P<status>out|doubtful|questionable|probable|gtd)\b(?P<rest>.*)$", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _bet_rows(bets: list[tuple[dict, str | None]], away: str, home: str) -> list[list]:
    rows = []
    for bet, result in bets:
        price, stake, clv = num(bet.get("price"), 0), num(bet.get("stake"), 2), num(bet.get("clv"), 4)
        rows.append([graded_cell(f"★ {_bet_label(bet, away, home)}", result),
                     None if price is None else f"{price:+.0f}", None if stake is None else f"{stake:.2f} u",
                     None if result is None else f"{_profit(bet, result):+.2f} u", None if clv is None else f"{clv:+.1%}"])
    return rows


def _four(t: dict, opp: dict) -> dict[str, float | None]:
    """A team's day in the four factors' terms, from its box score and its opponent's."""
    poss, fga = num(t.get("possessions"), 1), num(t.get("fga"), 0)
    orb, opp_drb = num(t.get("orb"), 0), num(opp.get("drb"), 0)
    return {"poss": poss,
            "ppp": None if not poss else 100 * (num(t.get("pts"), 0) or 0) / poss,
            "efg": None if not fga else ((num(t.get("fgm"), 0) or 0) + 0.5 * (num(t.get("three_pm"), 0) or 0)) / fga,
            "tov": None if not poss else (num(t.get("tov"), 0) or 0) / poss,
            "orb": None if orb is None or opp_drb is None or orb + opp_drb == 0 else orb / (orb + opp_drb),
            "ftr": None if not fga else (num(t.get("fta"), 0) or 0) / fga}


def result_card(d: dict, g: dict, box: dict | None, day: str) -> dict:
    """The card for one graded game (a day file's row `d`, graded as `g`): the final under the
    twist's expected score and Barttorvik's alone, the picks and bets, each layer's push and
    whether it moved the margin toward the final, who was out and whether he played, and how the
    game was played."""
    away, home = g["away"], g["home"]
    pa, ph = g["proj_away"], g["proj_home"]
    ta, th = num(d.get("torvik_away"), 4), num(d.get("torvik_home"), 4)
    p_home = num(d.get("p_home_win"), 4)
    winner = home if g["home_pts"] > g["away_pts"] else away
    actual = g["home_pts"] - g["away_pts"]
    ot = "" if not g["ot"] else (" (OT)" if g["ot"] == 1 else f" ({g['ot']}OT)")
    vs = "vs" if _flag(d.get("neutral")) else "@"

    def lean(margin):
        return "even" if abs(margin) < 0.05 else f"{home if margin > 0 else away} by {abs(margin):.1f}"

    notes = []
    if pa is not None and ph is not None:
        notes.append(f"With the twist the model expected {away} {pa:.1f} – {home} {ph:.1f} ({lean(ph - pa)})"
                     + (f"; Barttorvik alone, {away} {ta:.1f} – {home} {th:.1f} ({lean(th - ta)})" if ta is not None and th is not None else "")
                     + f". It ended {g['away_pts']}–{g['home_pts']}{ot}, {winner} by {abs(actual)}.")
        closer = _closer(g)
        if closer:
            notes.append("Closer to the final margin: " + ("neither, they were level" if closer["v"] == "even" else closer["v"].replace(" by ", ", by ")) + ".")
    picks = [["Win pick", graded_cell(f"{g['pick']} {g['pick_p']:.0%}", g["pick_res"])],
             ["vs Spread", graded_cell(*g["ats"]) if g["ats"] else None],
             ["vs Total", graded_cell(*g["ou"]) if g["ou"] else None]]
    sections = [{"title": "How it went", "kind": "notes", "items": notes or ["Final."]},
                {"title": "The picks", "kind": "table", "columns": ["", "Pick"], "rows": [x for x in picks if x[1] is not None],
                 "note": "The twist's picks; against the market, the side of the published spread and total it gave over 50%."}]
    if g["bets"]:
        sections.append({"title": "Bets", "kind": "table", "columns": ["Bet", "Price", "Stake", "Profit", "CLV"],
                         "rows": _bet_rows(g["bets"], away, home),
                         "note": "CLV: the bet's expected return at the closing line's no-vig odds."})
    rows = []
    if pa is not None and ph is not None:
        rows.append(versus_row("Points · twist", pa, g["away_pts"], ph, g["home_pts"], proj_digits=1, delta="diff"))
    if ta is not None and th is not None:
        rows.append(versus_row("Points · Barttorvik", ta, g["away_pts"], th, g["home_pts"], proj_digits=1, delta="diff"))
    if rows:
        sections.append({"title": "Projected against actual", "kind": "versus", "rows": rows,
                         "note": "The bar is what each team scored (overtime included), the tick each variant's expected points."})
    if pa is not None and ph is not None:
        twist = ph - pa
        layers = []
        for key, label in LAYERS:
            m = num(d.get(f"{key}_pts"), 3)
            if m is None or abs(m) < 0.05:
                continue
            helped = abs(twist - actual) < abs(twist - m - actual)
            layers.append([label, f"{home if m > 0 else away} +{abs(m):.1f}",
                           {"v": "toward the final ✓" if helped else "away from it ✗", "tone": "good" if helped else "bad"}])
        if layers:
            sections.append({"title": "The twist's layers", "kind": "table", "columns": ["Layer", "Moved the margin", "Result"],
                             "rows": layers, "note": "Each layer's push on the expected margin, and whether the margin came "
                                                     "nearer the final with it than without it."})
    players = (box or {}).get("players", [])
    minutes = {(int(r["team_id"]), _norm(r.get("name"))): num(r.get("minutes"), 0) or 0 for r in players}
    out = []
    for side, team, team_id in (("away", away, num(d.get("away_id"), 0)), ("home", home, num(d.get("home_id"), 0))):
        for entry in (text(d.get(f"{side}_out")) or "").split(", "):
            m = OUT_ENTRY.match(entry.strip()) if entry.strip() else None
            if not m:
                continue
            played = minutes.get((int(team_id or 0), _norm(m["name"])))
            source = " (box score)" if "box" in m["rest"] else ""
            out.append([team, m["name"], m["status"].lower() + source,
                        f"played {played:.0f} min" if played else "didn't play"])
    if out:
        sections.append({"title": "Who was out", "kind": "table", "columns": ["Team", "Player", "Listed", "In the game"],
                         "rows": out, "note": "Your availability file, else a rotation player who missed his team's last "
                                              "game (box score), as the prediction had them."})
    teams = (box or {}).get("teams", {})
    ida, idh = (int(num(d.get(k), 0) or 0) for k in ("away_id", "home_id"))
    if ida in teams and idh in teams:
        fa, fh = _four(teams[ida], teams[idh]), _four(teams[idh], teams[ida])
        rows = []
        for label, key, fmt, lower in (("Possessions", "poss", "{:.0f}", False), ("Points per 100", "ppp", "{:.1f}", False),
                                       ("Effective FG%", "efg", "{:.0%}", False), ("Turnover rate", "tov", "{:.0%}", True),
                                       ("Off. rebound %", "orb", "{:.0%}", False), ("Free-throw rate", "ftr", "{:.0%}", False)):
            x, y = fa[key], fh[key]
            if x is None or y is None:
                continue
            rows.append({"label": label, "away": x, "home": y, "awayText": fmt.format(x), "homeText": fmt.format(y),
                         **({"lower": True} if lower else {}), **({"max": 1} if fmt.endswith("%}") else {})})
        if rows:
            sections.append({"title": "How it was played", "kind": "compare", "rows": rows,
                             "note": "From the box score: the four factors (shooting, turnovers, offensive rebounds, free "
                                     "throws) and the pace; the brighter number is the better one."})
    if players:
        top = []
        for team, team_id in ((away, ida), (home, idh)):
            mine = sorted((r for r in players if int(r["team_id"]) == team_id), key=lambda r: -(num(r.get("pts"), 0) or 0))[:3]
            top += [[team, text(r.get("name")), f"{num(r.get('minutes'), 0) or 0:.0f}", num(r.get("pts"), 0),
                     num(r.get("reb"), 0), num(r.get("ast"), 0),
                     f"{num(r.get('three_pm'), 0) or 0:.0f}/{num(r.get('three_pa'), 0) or 0:.0f}"] for r in mine]
        if top:
            sections.append({"title": "Top scorers", "kind": "table",
                             "columns": ["Team", "Player", "Min", "Pts", "Reb", "Ast", "3PT"], "rows": top})
    return {
        "title": f"{away} {vs} {home}",
        "subtitle": f"{_day_label(day)} · Final{ot}",
        "away": {"name": away},
        "home": {"name": home},
        **({"score": {**sc, "label": sc["label"].replace("Final", f"Final{ot}", 1)}}
           if (sc := final_score(g["away_pts"], g["home_pts"], pa, ph)) else {}),
        **({"win": {"away": round(1 - p_home, 4), "home": p_home, "note": f"Before tip-off (the twist's) · {winner} won"}}
           if p_home is not None else {}),
        "sections": sections,
    }


def _graded(lean, won: bool | None) -> tuple[str, str] | None:
    if lean is None:
        return None
    return f"{lean[0]} {lean[1]:.0%}", "P" if won is None else ("W" if won else "L")


def graded_games(frames: dict[str, pd.DataFrame], days: list[str], finals: dict, bets: dict,
                 boxes: dict | None = None) -> list[dict]:
    """Every published prediction of a finished game, graded: the twist's win pick, probability and
    expected score, Barttorvik alone's margin beside it, and the twist's side of the market's lines;
    each with its card."""
    games = []
    for day in days:
        for d in frames[day].to_dict("records"):
            p = num(d.get("p_home_win"), 6)
            final = finals.get(text(d.get("game_id")))
            if p is None or final is None:
                continue
            away_pts, home_pts, ot = final
            away, home = text(d.get("away")), text(d.get("home"))
            am, hm = num(d.get("away_mean"), 4), num(d.get("home_mean"), 4)
            ta, th = num(d.get("torvik_away"), 4), num(d.get("torvik_home"), 4)
            home_won, y = home_pts > away_pts, float(home_pts > away_pts)
            pick = home if p >= 0.5 else away
            q = min(max(p, 1e-12), 1 - 1e-12)
            line, total_line = num(d.get("spread_line")), num(d.get("total_line"))
            ats, ou = _ats(d, away, home), _ou(d)
            cover = None if line is None or home_pts - away_pts + line == 0 else home_pts - away_pts + line > 0
            over = None if total_line is None or home_pts + away_pts == total_line else home_pts + away_pts > total_line
            game = {
                "id": text(d.get("game_id")),
                "day": day, "order": text(d.get("start_ts")) or "",
                "away": away, "home": home, "away_pts": away_pts, "home_pts": home_pts, "ot": int(ot or 0),
                "proj_away": am, "proj_home": hm,
                "pick": pick, "pick_p": max(p, 1 - p), "pick_res": "W" if (pick == home) == home_won else "L",
                "accuracy": float((pick == home) == home_won),
                "log_loss": -(y * math.log(q) + (1 - y) * math.log(1 - q)), "brier": (p - y) ** 2,
                "margin_err": None if am is None or hm is None else (hm - am) - (home_pts - away_pts),
                "torvik_err": None if ta is None or th is None else (th - ta) - (home_pts - away_pts),
                "total_err": None if am is None or hm is None else (hm + am) - (home_pts + away_pts),
                "ats": _graded(ats, None if cover is None or ats is None else cover == ats[0].startswith(home + " ")),
                "ou": _graded(ou, None if over is None or ou is None else over == ou[0].startswith("Over")),
                "bets": [(b, BET_RESULT.get(text(b.get("result")))) for b in bets.get(text(d.get("game_id")), [])],
            }
            if boxes is not None:
                game["card"] = result_card(d, game, boxes.get(text(d.get("game_id"))), day)
            games.append(game)
    return games


def _closer(g: dict) -> dict | None:
    """Whose expected margin came nearer the final: 'twist by 2.3' (good) or 'Barttorvik by 1.1'."""
    if g["margin_err"] is None or g["torvik_err"] is None:
        return None
    gap = round(abs(g["torvik_err"]) - abs(g["margin_err"]), 2)
    if abs(gap) < 0.05:
        return {"v": "even", "sort": 0.0, "tone": "muted"}
    return {"v": f"{'twist' if gap > 0 else 'Barttorvik'} by {abs(gap):.1f}", "sort": gap, "tone": "good" if gap > 0 else "bad"}


def _tally(results) -> tuple[int, int, int]:
    results = list(results)
    return results.count("W"), results.count("L"), results.count("P")


def _rec(t) -> str:
    w, l, p = t
    return f"{w}–{l}" + (f"–{p}" if p else "")


def _profit(bet: dict, result: str | None) -> float:
    stake, price = num(bet.get("stake"), 6) or 0.0, num(bet.get("price"), 0)
    if result == "W" and price:
        return stake * (price / 100 if price > 0 else 100 / -price)
    return -stake if result == "L" else 0.0


def _mae(errors) -> float | None:
    errors = [abs(e) for e in errors if e is not None]
    return sum(errors) / len(errors) if errors else None


def _numbers(games: list[dict]) -> dict:
    n = len(games)
    settled = [(b, res) for g in games for b, res in g["bets"] if res]
    staked = sum(num(b.get("stake"), 6) or 0.0 for b, _ in settled)
    clvs = [c for b, _ in settled if (c := num(b.get("clv"), 6)) is not None]
    return {
        "n": n, "su": _tally(g["pick_res"] for g in games), "accuracy": sum(g["accuracy"] for g in games) / n,
        "log_loss": sum(g["log_loss"] for g in games) / n, "brier": sum(g["brier"] for g in games) / n,
        "margin_mae": _mae(g["margin_err"] for g in games), "torvik_mae": _mae(g["torvik_err"] for g in games),
        "total_mae": _mae(g["total_err"] for g in games),
        "ats": _tally(g["ats"][1] for g in games if g["ats"]), "ou": _tally(g["ou"][1] for g in games if g["ou"]),
        "bets": _tally(res for _, res in settled),
        "roi": sum(_profit(b, res) for b, res in settled) / staked if staked else None,
        "clv": sum(clvs) / len(clvs) if clvs else None,
    }


def _vs_half(t) -> dict | None:
    w, l, _ = t
    if w + l == 0:
        return None
    rate = w / (w + l)
    return {"v": f"{_rec(t)} ({rate:.0%})", "sort": round(rate, 4), **({"tone": "good" if rate > 0.5 else "bad"} if rate != 0.5 else {})}


def _lower_better(value: float, metric: str) -> dict:
    return {"v": f"{value:.3f}", "sort": round(value, 6), "tone": "good" if value < COIN_FLIP[metric] else "bad"}


def _record_cell(r: dict | None) -> dict | None:
    """{w, l, p} from records.json as a '55–40 (58%)' cell toned against a coin flip."""
    return None if not r else _vs_half((r["w"], r["l"], r["p"]))


def _pair(with_: float | None, without: float | None) -> dict | None:
    """'11.05 / 11.88' (with / without), toned by whether the layer helped (lower is better)."""
    if with_ is None or without is None:
        return None
    return {"v": f"{with_:.2f} / {without:.2f}", "sort": round(without - with_, 4),
            **({"tone": "good" if with_ < without else "bad"} if with_ != without else {})}


def layer_slates(records: dict | None, season: int, updated: str) -> list[Slate]:
    """The season's records from the model's own grading (outputs/records.json): Barttorvik alone
    against the twist, and each layer with it and without it on the games it moved, every game
    picked against the closing line."""
    if not records or not records.get("games"):
        return []
    variants = []
    for key, label in (("torvik", "Barttorvik alone"), ("twist", "With the twist")):
        s = records.get(key)
        if s:
            variants.append({"variant": label, "games": s["games"], "ats": _record_cell(s["ats"]), "ou": _record_cell(s["ou"]),
                             "margin": num(s["margin_rmse"], 2), "total": num(s["total_mae"], 2),
                             "logLoss": _lower_better(s["log_loss"], "log_loss") if s.get("log_loss") is not None else None})
    twist = records.get("twist") or {}
    if twist.get("close_margin_rmse") is not None:
        variants.append({"variant": "The closing line", "games": twist["lined"], "ats": None, "ou": None,
                         "margin": num(twist["close_margin_rmse"], 2), "total": num(twist["close_total_mae"], 2), "logLoss": None})
    through = text(records.get("through"))
    head = f"{_season_label(season)} · {records['games']} graded games" + (f" through {_day_label(through)}" if through else "")
    out = [Slate(f"{season}-twist", "Torvik vs twist", {
        "subtitle": head + " · every game picked against the closing line (ATS, O/U); margin RMSE and total MAE in points",
        "updated": updated,
        "columns": [{"key": "variant", "label": ""}, {"key": "games", "label": "Games", "align": "right"},
                    {"key": "ats", "label": "ATS", "align": "right"}, {"key": "ou", "label": "O/U", "align": "right"},
                    {"key": "margin", "label": "Margin RMSE", "align": "right", "format": "number"},
                    {"key": "total", "label": "Total MAE", "align": "right", "format": "number"},
                    {"key": "logLoss", "label": "Log loss", "align": "right"}],
        "rows": variants,
    })]
    layers = [r for r in records.get("layers") or [] if r.get("games")]
    if layers:
        labels = dict(LAYERS)
        out.append(Slate(f"{season}-layers", "Twist layers", {
            "subtitle": head + " · each layer with it / without it, on the games it moved (lower is better; green: it helped)",
            "updated": updated,
            "columns": [{"key": "layer", "label": "Layer"}, {"key": "games", "label": "Games", "align": "right"},
                        {"key": "move", "label": "Avg move", "align": "right", "format": "number"},
                        {"key": "margin", "label": "Margin RMSE", "align": "right", "sortable": True},
                        {"key": "atsWith", "label": "ATS with", "align": "right"},
                        {"key": "atsWithout", "label": "ATS without", "align": "right"},
                        {"key": "total", "label": "Total MAE", "align": "right", "sortable": True}],
            "rows": [{"layer": labels.get(r["layer"], r["layer"]), "games": r["games"], "move": num(r.get("avg_move"), 2),
                      "margin": _pair(r.get("margin_rmse_with"), r.get("margin_rmse_without")),
                      "atsWith": _record_cell(r.get("ats_with")), "atsWithout": _record_cell(r.get("ats_without")),
                      "total": _pair(r.get("total_mae_with"), r.get("total_mae_without"))} for r in layers],
        }))
    return out


def publish_results(view_dir: Path, games: list[dict], *, season: int, updated: str, records: dict | None) -> dict:
    """Write the CBB results view: season tiles, 'All days', the variant and layer records, one
    slate per day. Returns the season's numbers (for the card)."""
    games = sorted(games, key=lambda g: (g["day"], g["order"]))
    total = _numbers(games)
    acc = total["accuracy"]
    stats = [
        {"label": "Straight up", "value": _rec(total["su"]), "detail": f"{acc:.1%}",
         **({"tone": "good" if acc > 0.5 else "bad"} if acc != 0.5 else {})},
        {"label": "Log loss", "value": f"{total['log_loss']:.3f}", "detail": "coin flip 0.693",
         "tone": "good" if total["log_loss"] < COIN_FLIP["log_loss"] else "bad"},
        {"label": "Margin MAE", "value": "—" if total["margin_mae"] is None else f"{total['margin_mae']:.1f}",
         "detail": "twist" + ("" if total["torvik_mae"] is None else f" · Barttorvik alone {total['torvik_mae']:.1f}"),
         **({} if total["margin_mae"] is None or total["torvik_mae"] is None or total["margin_mae"] == total["torvik_mae"]
            else {"tone": "good" if total["margin_mae"] < total["torvik_mae"] else "bad"})},
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
                      "detail": " · ".join(x for x in (None if roi is None else f"ROI {roi:+.1%}",
                                                       None if clv is None else f"CLV {clv:+.1%}") if x) or "—",
                      **({"tone": "good" if roi > 0 else "bad"} if roi else {})})
    summary = {
        "title": f"{_season_label(season)} season to date · {total['n']} games graded",
        "stats": stats,
        "note": "Graded the way the model grades itself: straight up is the home side at 50% or more, else the "
                "away side; log loss scores the win probability (lower is better); margin MAE compares the "
                "expected margin with the final (overtime included), the twist's beside Barttorvik alone's. "
                "Against the market, the published spread and total are graded as the side the twist gives over "
                "50%. 'Torvik vs twist' and 'Twist layers' are the model's own records against the closing line.",
    }

    any_bets = any(g["bets"] for g in games)
    game_cols = scoreboard_columns("Final") + [
        {"key": "proj", "label": "Proj.", "align": "right"},
        {"key": "pick", "label": "Win Pick", "align": "right", "sortable": True},
        {"key": "margin", "label": "Margin: twist → final", "align": "right", "sortable": True},
        {"key": "closer", "label": "Closer margin", "align": "right", "sortable": True},
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
                "id": g["id"],
                **scoreboard(g["away"], g["home"], g["away_pts"], g["home_pts"], digits=0),
                "sep": "–" if not g["ot"] else ("OT" if g["ot"] == 1 else f"{g['ot']}OT"),
                "proj": None if pa is None or ph is None else f"{pa:.1f} – {ph:.1f}",
                "pick": graded_cell(f"{g['pick']} {g['pick_p']:.0%}", g["pick_res"]),
                "margin": None if g["margin_err"] is None else {
                    "v": f"{ph - pa:+.1f} → {g['home_pts'] - g['away_pts']:+d}", "sort": round(abs(g["margin_err"]), 4)},
                "closer": _closer(g),
                **{key: graded_cell(*g[key]) for key in ("ats", "ou") if g[key]},
                **({"bet": bet_cells[0]} if bet_cells else {}),
                "flagged": bool(g["bets"]),
            })
        line = [f"{r['n']} game{'s' * (r['n'] != 1)}", f"straight up {_rec(r['su'])}", f"log loss {r['log_loss']:.3f}"]
        if r["margin_mae"] is not None:
            line.append(f"margin MAE {r['margin_mae']:.1f}" + ("" if r["torvik_mae"] is None else f" (Barttorvik alone {r['torvik_mae']:.1f})"))
        slates.append(Slate(day, _day_label(day), {
            "subtitle": f"{_day_label(day)}, {_season_label(season)} — " + " · ".join(line)
                        + " · margins are the home side's; Closer margin = whose expected margin came nearer the final, the twist's or Barttorvik's alone",
            "updated": updated,
            "search": "Search teams",
            **({"filters": [{**FLAGGED_FILTER, "label": "★ bets only"}]} if any(row["flagged"] for row in rows) else {}),
            "columns": game_cols,
            "rows": rows,
        }, {g["id"]: g["card"] for g in day_games if g.get("card")} or None))
        by_day.append({
            "day": {"v": _day_label(day), "sort": day}, "games": r["n"], "su": _vs_half(r["su"]),
            "logLoss": _lower_better(r["log_loss"], "log_loss"),
            "mae": None if r["margin_mae"] is None else {"v": f"{r['margin_mae']:.1f}", "sort": round(r["margin_mae"], 4)},
            "torvik": None if r["torvik_mae"] is None else {"v": f"{r['torvik_mae']:.1f}", "sort": round(r["torvik_mae"], 4)},
            "ats": _vs_half(r["ats"]), "ou": _vs_half(r["ou"]), **({"bets": _vs_half(r["bets"])} if any_bets else {}),
        })
    overview = {
        "subtitle": f"{_season_label(season)} · record by day",
        "updated": updated,
        "columns": [
            {"key": "day", "label": "Day", "sortable": True}, {"key": "games", "label": "Games", "align": "right"},
            {"key": "su", "label": "Straight up", "align": "right", "sortable": True},
            {"key": "logLoss", "label": "Log loss", "align": "right", "sortable": True},
            {"key": "mae", "label": "Margin MAE", "align": "right", "sortable": True},
            {"key": "torvik", "label": "Barttorvik MAE", "align": "right", "sortable": True},
            {"key": "ats", "label": "vs Spread", "align": "right", "sortable": True},
            {"key": "ou", "label": "vs Total", "align": "right", "sortable": True},
            *([{"key": "bets", "label": "★ Bets", "align": "right", "sortable": True}] if any_bets else []),
        ],
        "rows": by_day,
    }
    publish_slates(view_dir, [Slate(f"{season}-all", "All days", overview), *layer_slates(records, season, updated), *slates],
                   season=season, label=f"{_season_label(season)} Season", unit="day", latest=slates[-1].id, summary=summary)
    return total


def read_records(outputs: Path, season: int) -> dict | None:
    try:
        records = json.loads((outputs / "records.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return records if records.get("season") == season else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", help="day the site opens on, YYYY-MM-DD (default: latest predicted)")
    ap.add_argument("--model", type=Path, default=MODEL, help="the CBB model's repo")
    ap.add_argument("--site-dir", type=Path, default=SITE_DATA / "cbb", help="site public/data/cbb dir")
    args = ap.parse_args()
    outputs = args.model / "outputs"

    frames: dict[str, pd.DataFrame] = {}
    paths: dict[str, Path] = {}
    for path in sorted((outputs / "daily").glob("*_predictions.csv")):
        m = DAILY.match(path.name)
        if not m:
            continue
        df = read_csv_stable(path)
        if len(df):
            frames[m.group("day")], paths[m.group("day")] = df, path
    rankings = outputs / "rankings.csv"
    season = max((_season_of(day) for day in frames), default=None)
    if season is None:  # before the first predicted day: the rankings' season, else today's
        as_of = text(pd.read_csv(rankings, usecols=["as_of"])["as_of"].iloc[0]) if rankings.exists() else None
        season = _season_of((as_of or date.today().isoformat())[:10])
    publish = sorted(day for day in frames if _season_of(day) == season)
    # The early look: a coming day the gameday task hasn't predicted yet (`cbb early`).
    today = datetime.now(EASTERN).date().isoformat()
    early: dict[str, pd.DataFrame] = {}
    for path in sorted((outputs / "early").glob("*.csv")):
        m = EARLY.match(path.name)
        if not m or m.group("day") < today or m.group("day") in frames or _season_of(m.group("day")) != season:
            continue
        df = read_csv_stable(path)
        if len(df):
            early[m.group("day")], paths[m.group("day")] = df, path
    days = sorted([*publish, *early])
    ahead = [day for day in days if day >= today]
    opens = args.day if args.day in days else (ahead[0] if ahead else (days[-1] if days else None))
    print(f"Exporting CBB {_season_label(season)}" + (f", opens on {opens}" if opens else ", no day predicted yet")
          + f"\n  from: {outputs}\n  to:   {args.site_dir}")

    slates = []
    for day in days:
        if day in early:
            df, bets, label = early[day], pd.DataFrame(columns=["game_id"]), f"{_day_label(day)} · early look"
        else:
            bets_path = paths[day].with_name(f"{day}_bets.csv")
            df, label = frames[day], _day_label(day)
            bets = read_csv_stable(bets_path) if bets_path.exists() else pd.DataFrame(columns=["game_id"])
        slates.append(Slate(day, label, build_day(df, bets, day, season, file_date(paths[day]), early=day in early),
                            build_cards(df, bets, day) or None))
    publish_slates(args.site_dir / "games", slates, season=season, label=f"{_season_label(season)} Season", unit="day", latest=opens)
    sources = [paths[day] for day in days]

    sim, ncaa = newest_sim(outputs, season), newest_sim(outputs, season, "ncaa")
    favorite = None
    if sim is not None:
        write_table(args.site_dir / "season.json", build_season(sim, season, ncaa))
        sources += [sim] + ([ncaa] if ncaa is not None else [])
        table = pd.read_csv(sim)
        best = table.loc[table["p_title"].idxmax()]
        favorite = {"label": "Surest title", "value": text(best["team"]),
                    "detail": f"{num(best['p_title'], 4):.0%} to win the {CONFERENCES.get(text(best['conference']) or '', text(best['conference']))}"}
    else:
        print(f"  [skip] no {season} season simulation in {outputs / 'sims'}: run `cbb season`")

    top = None
    if rankings.exists():
        write_table(args.site_dir / "rankings.json", build_rankings(rankings, season))
        sources.append(rankings)
        best = pd.read_csv(rankings).sort_values("rank").iloc[0]
        top = {"label": "#1", "value": text(best["team"]), "detail": f"net {num(best['net'], 1):+.1f} per 100"}
    else:
        print(f"  [skip] no {rankings.name}: run `cbb rankings`")

    finals, bets, opening = read_db(args.model, season)
    graded = graded_games(frames, publish, finals, bets, read_boxes(args.model, season))
    record = None
    if graded:
        db = args.model / "data" / "cbb.sqlite"
        record = publish_results(args.site_dir / "results", graded, season=season, updated=file_date(db),
                                 records=read_records(outputs, season))
        sources.append(db)
    else:
        publish_slates(args.site_dir / "results", [], season=season, label=f"{_season_label(season)} Season",
                       unit="day", latest=None)

    straight_up = None if record is None else {
        "label": "Straight up", "value": _rec(record["su"]), "detail": f"{record['accuracy']:.1%}",
        **({"tone": "good" if record["accuracy"] > 0.5 else "bad"} if record["accuracy"] != 0.5 else {})}
    ats = None if record is None or not sum(record["ats"][:2]) else {
        "label": "vs Spread", "value": _rec(record["ats"]), "detail": f"{record['ats'][0] / sum(record['ats'][:2]):.1%}"}
    headline = (_day_label(opens) if opens else
                f"Season opens {_day_label(opening)}" if opening else f"{_season_label(season)} season")
    write_card(args.site_dir, headline=headline, updated=max((file_date(p) for p in sources), default=None),
               stats=[s for s in (straight_up, ats or top, None if record else favorite) if s][:2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
