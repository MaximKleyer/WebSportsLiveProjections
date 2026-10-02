#!/usr/bin/env python
"""NHL adapter: NHL_Projection_Model's outputs -> the site's JSON (via site_export).

Reads the model's outputs/ - plus, for goalie names, the NHL game-centre pages
it caches under data/raw/ - and writes public/data/nhl/:

    games/          one slate per DAY           outputs/daily/<date>_predictions.csv
                                                (+ <date>_bets.csv: flagged bets)
    season.json     standings + playoff odds    outputs/season_sim.csv
    rankings.json   power rankings              outputs/power_rankings.csv
    results/        those daily predictions     final scores + settled bets from the
                    graded, one slate per day   model's database (data/nhl.sqlite, read-only)
    card.json       the landing card's line

scripts/update-data.ps1 runs this with the model's own venv Python (pandas):

    <model>\\.venv\\Scripts\\python.exe scripts\\exporters\\nhl.py [--day 2026-10-02]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sqlite3
import sys
from datetime import date
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_export import (  # noqa: E402  (the site's shared export toolkit)
    FLAGGED_FILTER, SITE_DATA, Slate, file_date, graded_cell, num, publish_slates, scoreboard,
    scoreboard_columns, text, write_card, write_table,
)

MODEL = Path(r"C:\Users\maxim\NHL_Projection_Model")
EASTERN = ZoneInfo("America/New_York")
DAILY = re.compile(r"(?P<day>\d{4}-\d{2}-\d{2})_predictions\.csv$")
GOALIE_ID = re.compile(r"\b\d{7}\b")
SEARCH = "Search teams or goalies"


def _season_label(season: int) -> str:
    """2026 -> '2026–27' (an NHL season id 20262027 starts with its first year)."""
    return f"{season}–{(season + 1) % 100:02d}"


def _day_label(day: str) -> str:
    """'2026-10-02' -> 'Fri Oct 2'."""
    d = date.fromisoformat(day)
    return f"{d:%a} {d:%b} {d.day}"


def _fair_odds(p):
    """A win probability as American odds without a margin: 0.54 -> '-117'."""
    if p is None or not 0 < p < 1:
        return None
    return f"{(-100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p):+.0f}"


def _start(ts):
    """'2026-10-02T22:30:00Z' -> a cell showing '6:30 PM' (ET) that sorts by the time."""
    if text(ts) is None:
        return None
    et = pd.Timestamp(ts).tz_convert(EASTERN)
    return {"v": f"{et.hour % 12 or 12}:{et.minute:02d} {'AM' if et.hour < 12 else 'PM'}",
            "sort": text(ts)}


def goalie_names(model: Path, game_ids) -> dict[int, str]:
    """goalie id -> name, from the game-centre pages the model cached for these
    games - the same pages and fields its daily report takes names from."""
    names: dict[int, str] = {}
    for game_id in game_ids:
        page = model / "data" / "raw" / "api-web.nhle.com" / "v1" / "gamecenter" / str(game_id) / "landing.json.gz"
        try:
            landing = json.loads(gzip.decompress(page.read_bytes()))
        except (OSError, EOFError, ValueError):
            continue  # not cached: those goalies keep their ids
        comparison = landing.get("matchup", {}).get("goalieComparison", {})
        for side in ("homeTeam", "awayTeam"):
            for goalie in comparison.get(side, {}).get("leaders", []):
                names[int(goalie["playerId"])] = goalie["name"]["default"]
    return names


def _starter(starters, names: dict[int, str]):
    """The model's starter text - '8476914 91%, 8478048 9%', '8476914 (confirmed)'
    or 'team mix' - as the likeliest starter by name: 'J. Korpisalo 91%'."""
    s = text(starters)
    if s is None or s == "team mix":
        return None
    first = s.split(",")[0].strip()
    return GOALIE_ID.sub(lambda m: names.get(int(m.group()), m.group()), first)


def _bet_label(bet: dict, away: str, home: str) -> str:
    """A flagged bet as the site shows it: 'DET ML', 'DET -1.5', 'OVER 6.5'."""
    market, side, line = text(bet.get("market")), text(bet.get("side")), num(bet.get("line"))
    team = {"home": home, "away": away}.get(side)
    if market == "ml":
        return f"{team} ML"
    if market == "puckline" and line is not None:
        return f"{team} {line:+g}"
    return f"{(side or '').upper()} {'' if line is None else f'{line:g}'}".strip()


def build_day(path: Path, day: str, names: dict[int, str], season: int) -> dict:
    df = pd.read_csv(path)
    bets_path = path.with_name(f"{day}_bets.csv")
    bets = pd.read_csv(bets_path) if bets_path.exists() else pd.DataFrame()
    bets_by_game: dict = {}
    for b in bets.to_dict("records"):
        bets_by_game.setdefault(b.get("game_id"), []).append(b)

    puck = next((c.removeprefix("p_home_minus_") for c in df.columns if c.startswith("p_home_minus_")), "1.5")
    overs = [c for c in df.columns if re.fullmatch(r"p_over_\d+(\.\d+)?", c)]
    predicted = df[df["p_home_win"].notna()] if "p_home_win" in df else df.iloc[0:0]
    skipped = len(df) - len(predicted)
    has_market = any(predicted.get(c, pd.Series(dtype=float)).notna().any()
                     for c in ("market_p_home_win", "market_total_line"))

    rows = []
    for d in predicted.sort_values("start_ts").to_dict("records"):
        away, home = text(d.get("away")), text(d.get("home"))
        p_home = num(d.get("p_home_win"), 4)
        total = num(d.get("expected_total"), 2)
        home_fav = p_home is not None and p_home >= 0.5
        fav_cover = num(d.get(f"p_home_minus_{puck}" if home_fav else f"p_away_minus_{puck}"), 4)
        row = {
            "start": _start(d.get("start_ts")),
            **scoreboard(away, home, d.get("away_goals"), d.get("home_goals")),
            "homeWin": p_home,
            "fair": _fair_odds(p_home),
            "puck": None if fav_cover is None else {"v": f"{home if home_fav else away} {fav_cover:.0%}",
                                                     "sort": fav_cover},
            # two decimals, as the model reports it: 6.13 vs 6.35 matters against a 6.5 line
            "total": None if total is None else {"v": f"{total:.2f}", "sort": total},
            **{c: num(d.get(c), 4) for c in overs},
        }
        # Each side's likeliest starting goalie, under the team (searchable too).
        for key, side in (("awayTeam", "away"), ("homeTeam", "home")):
            goalie = _starter(d.get(f"{side}_starter"), names)
            if goalie and row[key]:
                row[key] = {"v": row[key], "sub": goalie}
        if has_market:
            line, p_over = num(d.get("market_total_line")), num(d.get("p_over_market_line"), 4)
            row["mktHome"] = num(d.get("market_p_home_win"), 4)
            row["mktTotal"] = None if line is None or p_over is None else {
                "v": f"{line:g}: over {p_over:.0%}", "sort": p_over}
        game_bets = bets_by_game.get(d.get("game_id"), [])
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
        {"key": "puck", "label": f"Fav -{puck}", "align": "right", "sortable": True},
        {"key": "total", "label": "Proj. Total", "align": "right", "sortable": True},
        *({"key": c, "label": f"Over {c.removeprefix('p_over_')}", "align": "right", "format": "percent",
           "sortable": True} for c in overs),
    ]
    if has_market:
        columns += [
            {"key": "mktHome", "label": "Mkt Home", "align": "right", "format": "percent", "sortable": True},
            {"key": "mktTotal", "label": "Mkt Total", "align": "right", "sortable": True},
        ]
    any_bets = any(r["flagged"] for r in rows)
    if any_bets:
        columns.append({"key": "bet", "label": "Bet (edge)", "align": "right", "sortable": True})

    note = f" · {skipped} started before the model ran" if skipped else ""
    return {
        "subtitle": f"{_day_label(day)}, {_season_label(season)} — {len(rows)} games · times ET · "
                    f"score = expected goals · fair ML is the home side's{' · ★ = flagged bet' if any_bets else ''}"
                    f"{note}",
        "updated": file_date(path),
        "search": SEARCH,
        **({"filters": [{**FLAGGED_FILTER, "label": "★ bets only"}]} if any_bets else {}),
        "columns": columns,
        "rows": rows,
    }


def build_season(path: Path, season: int) -> dict:
    # Grouped by division on the site: East (Atlantic, Metropolitan) then West
    # (Central, Pacific), most projected points first.
    df = pd.read_csv(path).sort_values(["conference", "division", "points_mean"],
                                       ascending=[True, True, False])
    rows = []
    for d in df.to_dict("records"):
        means = [num(d.get(k), 4) or 0 for k in ("wins_mean", "losses_mean", "ot_losses_mean")]
        w, otl = round(means[0]), round(means[2])
        l = round(sum(means)) - w - otl  # the rest of the schedule, as the model's report counts it
        p10, p90 = num(d.get("points_p10"), 0), num(d.get("points_p90"), 0)
        rows.append({
            "team": text(d.get("name")),
            "division": text(d.get("division")),
            "record": f"{int(d.get('wins') or 0)}–{int(d.get('losses') or 0)}–{int(d.get('ot_losses') or 0)}",
            "points": int(d.get("points") or 0),
            "projRecord": {"v": f"{w}–{l}–{otl}", "sort": num(d.get("points_mean"))},
            "projPoints": num(d.get("points_mean")),
            "range": None if p10 is None or p90 is None else f"{p10:.0f}–{p90:.0f}",
            "playoffs": num(d.get("playoffs"), 4),
            "divTitle": num(d.get("division_title"), 4),
            "cup": num(d.get("series_4"), 4),
        })

    columns = [
        {"key": "team", "label": "Team"},
        {"key": "record", "label": "W–L–OTL", "align": "right"},
        {"key": "points", "label": "Pts", "align": "right", "sortable": True},
        {"key": "projRecord", "label": "Proj. Record", "align": "right", "sortable": True},
        {"key": "projPoints", "label": "Proj. Pts", "align": "right", "format": "number", "sortable": True},
        {"key": "range", "label": "Pts 10–90%", "align": "right"},
        {"key": "playoffs", "label": "Playoffs", "align": "right", "format": "percent", "sortable": True},
        {"key": "divTitle", "label": "Division", "align": "right", "format": "percent", "sortable": True},
        {"key": "cup", "label": "Cup", "align": "right", "format": "percent", "sortable": True},
    ]

    return {
        "subtitle": f"{_season_label(season)} projected standings — season simulation with playoff odds",
        "updated": file_date(path),
        "groupBy": "division",
        "search": "Search teams or divisions",
        "columns": columns,
        "rows": rows,
    }


def _trend_days(model: Path) -> int | None:
    """power_rankings.trend_days from the model's config: the trend's window."""
    try:
        import yaml  # in the model's venv; only for this label
        return int(yaml.safe_load((model / "config" / "nhl.yaml").read_text())["power_rankings"]["trend_days"])
    except Exception:
        return None


def build_rankings(path: Path, season: int, trend_days: int | None) -> dict:
    df = pd.read_csv(path).sort_values("rank")
    scores = [("overall", "Overall"), ("offense", "Offense"), ("defense", "Defense"),
              ("goalies", "Goalies"), ("five", "5v5"), ("pp", "PP"), ("pk", "PK"),
              ("discipline", "Discipline")]
    rows = []
    for d in df.to_dict("records"):
        trend = num(d.get("trend"), 2)
        r = 0 if trend is None else round(trend)
        rows.append({
            "rank": int(d["rank"]),
            "team": text(d.get("name")),
            "winPct": num(d.get("p_win"), 4),
            **{key: num(d.get(f"{key}_score"), 0) for key, _ in scores},
            "trend": None if trend is None else {
                "v": f"{r:+d}" if r else "0", "sort": trend,
                **({"tone": "good" if r > 0 else "bad"} if r else {})},
        })

    columns = [
        {"key": "rank", "label": "#", "align": "right", "sortable": True},
        {"key": "team", "label": "Team"},
        {"key": "overall", "label": "Overall", "align": "right", "format": "number", "sortable": True},
        {"key": "winPct", "label": "Win % vs avg", "align": "right", "format": "percent", "sortable": True},
        *({"key": key, "label": label, "align": "right", "format": "number", "sortable": True}
          for key, label in scores[1:]),
        {"key": "trend", "label": "Trend", "align": "right", "sortable": True},
    ]

    window = f"the last {trend_days} days" if trend_days else "recent days"
    return {
        "subtitle": f"{_season_label(season)} — scores 0–100 are percentiles among every team-season "
                    f"since 2021–22 (50 = average). Overall = offense + defense + goalies; 5v5, PP, PK "
                    f"and discipline are context. Trend = change in overall over {window}.",
        "updated": file_date(path),
        "columns": columns,
        "rows": rows,
    }


# --------------------------------------------------------------------------
# results: the published daily predictions graded against final scores
# --------------------------------------------------------------------------
# Graded the way the model grades itself (core/eval/metrics.py): accuracy (the
# side given over 50% won; exactly 50% scores half), log loss and Brier on the
# calibrated win probability, and totals MAE (expected goals in the final
# score - OT/SO winner +1 - against the actual total). With no betting line to
# pick against, totals are also graded as leans: the side of 5.5 / 6.5 the
# model gives over 50%. Settled bets (`nhl settle`) are added once there are any.
COIN_FLIP = {"log_loss": math.log(2), "brier": 0.25}
LEAN_LINES = (5.5, 6.5)
BET_RESULT = {"win": "W", "loss": "L", "push": "P"}


def read_results(model: Path, season: int) -> tuple[dict[int, tuple], dict[int, list[dict]]]:
    """From the model's database (opened read-only): game_id -> (away goals,
    home goals, REG/OT/SO) for the season's finished games, and pred_id -> its
    settled bets."""
    path = model / "data" / "nhl.sqlite"
    if not path.exists():
        return {}, {}
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        finals = {r["game_id"]: (r["away_score"], r["home_score"], r["end_type"]) for r in con.execute(
            "SELECT game_id, away_score, home_score, end_type FROM games "
            "WHERE season = ? AND home_score IS NOT NULL", (season * 10000 + season + 1,))}
        bets: dict[int, list[dict]] = {}
        for r in con.execute("SELECT pred_id, market, side, line, price, stake, result, clv FROM bets "
                             "WHERE result IS NOT NULL"):
            bets.setdefault(r["pred_id"], []).append(dict(r))
    finally:
        con.close()
    return finals, bets


def graded_games(frames: dict[str, pd.DataFrame], days: list[str], finals: dict,
                 bets: dict) -> list[dict]:
    """Every published prediction whose game has finished, each pick graded."""
    games = []
    for day in days:
        for d in frames[day].to_dict("records"):
            p = num(d.get("p_home_win"), 6)
            final = finals.get(int(d["game_id"]))
            if p is None or final is None:
                continue  # no pre-game prediction, or not played yet
            away_goals, home_goals, end = final
            away, home = text(d.get("away")), text(d.get("home"))
            home_won, y = home_goals > away_goals, float(home_goals > away_goals)
            pick = None if p == 0.5 else (home if p > 0.5 else away)
            q = min(max(p, 1e-15), 1 - 1e-15)  # keeps log() finite, as the model's metric does
            total, expected = away_goals + home_goals, num(d.get("expected_total"), 4)
            leans = {}
            for line in LEAN_LINES:
                p_over = num(d.get(f"p_over_{line:g}"), 4)
                if p_over is None or p_over == 0.5:
                    continue
                over = p_over > 0.5
                result = "P" if total == line else ("W" if (total > line) == over else "L")
                leans[line] = (f"{'OVER' if over else 'UNDER'} {max(p_over, 1 - p_over):.0%}", result)
            games.append({
                "day": day,
                "order": text(d.get("start_ts")) or "",
                "away": away,
                "home": home,
                "away_goals": away_goals,
                "home_goals": home_goals,
                "end": end,
                "proj_away": num(d.get("away_goals")),
                "proj_home": num(d.get("home_goals")),
                "pick": pick,
                "pick_p": max(p, 1 - p),
                "pick_res": None if pick is None else ("W" if (pick == home) == home_won else "L"),
                "accuracy": 0.5 if pick is None else float((pick == home) == home_won),
                "log_loss": -(y * math.log(q) + (1 - y) * math.log(1 - q)),
                "brier": (p - y) ** 2,
                "total": total,
                "expected_total": expected,
                "total_err": None if expected is None else expected - total,
                "leans": leans,
                "bets": [(b, BET_RESULT.get(text(b.get("result")))) for b in bets.get(d.get("pred_id"), [])],
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


def _numbers(games: list[dict]) -> dict:
    """The model's own metrics, plus lean and bet records, for a set of graded games."""
    n = len(games)
    errs = [g["total_err"] for g in games if g["total_err"] is not None]
    settled = [(b, res) for g in games for b, res in g["bets"] if res]
    staked = sum(num(b.get("stake"), 6) or 0.0 for b, _ in settled)
    clvs = [c for b, _ in settled if (c := num(b.get("clv"), 6)) is not None]
    return {
        "n": n,
        "su": _tally(g["pick_res"] for g in games),
        "accuracy": sum(g["accuracy"] for g in games) / n,
        "log_loss": sum(g["log_loss"] for g in games) / n,
        "brier": sum(g["brier"] for g in games) / n,
        "mae": sum(abs(e) for e in errs) / len(errs) if errs else None,
        "leans": {line: _tally(g["leans"][line][1] for g in games if line in g["leans"])
                  for line in LEAN_LINES},
        "bets": _tally(res for _, res in settled),
        "roi": sum(_profit(b, res) for b, res in settled) / staked if staked else None,
        "clv": sum(clvs) / len(clvs) if clvs else None,
    }


def _vs_half(t, rate=None) -> dict | None:
    """'5–3 (63%)' toned against picking at random; None when nothing was picked."""
    w, l, _ = t
    if rate is None:
        rate = w / (w + l) if (w + l) else None
    if rate is None:
        return None
    cell = {"v": f"{_rec(t)} ({rate:.0%})", "sort": round(rate, 4)}
    if rate != 0.5:
        cell["tone"] = "good" if rate > 0.5 else "bad"
    return cell


def _lower_better(value: float, metric: str, digits: int = 3) -> dict:
    """A log loss / Brier score toned against a coin flip (lower is better)."""
    return {"v": f"{value:.{digits}f}", "sort": round(value, 6),
            "tone": "good" if value < COIN_FLIP[metric] else "bad"}


def _backtest(model: Path) -> dict | None:
    """The model's walk-forward backtest (the 'Model' row of 'All scored seasons'
    in outputs/backtest_report.md): what live results should look like."""
    try:
        report = (model / "outputs" / "backtest_report.md").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^\| Model \| [\d,]+ \| ([\d.]+) \| ([\d.]+) \| ([\d.]+)% \| [^|]+\| [^|]+\| ([\d.]+) \|",
                  report, re.M)
    return None if not m else {"log_loss": float(m[1]), "brier": float(m[2]),
                               "accuracy": float(m[3]) / 100, "mae": float(m[4])}


def publish_results(view_dir: Path, games: list[dict], *, season: int, updated: str,
                    backtest: dict | None) -> dict:
    """Write the NHL results view - season-to-date tiles, an 'All days' table,
    one slate per day - and return the season's numbers (for the card)."""
    games = sorted(games, key=lambda g: (g["day"], g["order"]))
    total = _numbers(games)
    stats = [
        {"label": "Straight up", "value": _rec(total["su"]), "detail": f"{total['accuracy']:.1%}",
         **({"tone": "good" if total["accuracy"] > 0.5 else "bad"} if total["accuracy"] != 0.5 else {})},
        {"label": "Log loss", "value": f"{total['log_loss']:.3f}", "detail": "coin flip 0.693",
         "tone": "good" if total["log_loss"] < COIN_FLIP["log_loss"] else "bad"},
        {"label": "Brier score", "value": f"{total['brier']:.3f}", "detail": "coin flip 0.250",
         "tone": "good" if total["brier"] < COIN_FLIP["brier"] else "bad"},
        {"label": "Totals MAE", "value": "—" if total["mae"] is None else f"{total['mae']:.2f}",
         "detail": "avg miss, goals"},
    ]
    if sum(total["bets"]):
        roi, clv = total["roi"], total["clv"]
        stats.append({"label": "★ Bets", "value": _rec(total["bets"]),
                      "detail": " · ".join(x for x in (
                          None if roi is None else f"ROI {roi:+.1%}",
                          None if clv is None else f"CLV {clv:+.1%}") if x) or "—",
                      **({"tone": "good" if roi > 0 else "bad"} if roi else {})})
    bt = "" if not backtest else (
        f" Backtest, 2022–23 to 2025–26: {backtest['accuracy']:.1%} straight up, log loss "
        f"{backtest['log_loss']:.3f}, Brier {backtest['brier']:.3f}, totals MAE {backtest['mae']:.2f}.")
    summary = {
        "title": f"{_season_label(season)} season to date · {total['n']} games graded",
        "stats": stats,
        "note": "Graded the way the model grades itself: straight up is the side given over 50%; log "
                "loss and Brier score the win probability (lower is better); totals compare the "
                "expected goals in the final score (OT/SO winner +1) with the actual total. O/U leans "
                "are the side of 5.5 / 6.5 the model gives over 50%." + bt,
    }

    any_bets = any(g["bets"] for g in games)
    lean_key = {line: f"ou{line:g}".replace(".", "") for line in LEAN_LINES}  # 5.5 -> 'ou55'
    game_cols = scoreboard_columns("Final") + [
        {"key": "proj", "label": "Proj.", "align": "right"},
        {"key": "pick", "label": "Win Pick", "align": "right", "sortable": True},
        {"key": "total", "label": "Total: proj → final", "align": "right", "sortable": True},
        *({"key": lean_key[line], "label": f"O/U {line:g}", "align": "right", "sortable": True}
          for line in LEAN_LINES),
        *([{"key": "bet", "label": "Bet", "align": "right", "sortable": True}] if any_bets else []),
    ]
    slates, by_day = [], []
    for day, day_games in groupby(games, key=lambda g: g["day"]):
        day_games = list(day_games)
        r = _numbers(day_games)
        rows = []
        for g in day_games:
            pa, ph, err = g["proj_away"], g["proj_home"], g["total_err"]
            bet_cells = [graded_cell(f"★ {_bet_label(b, g['away'], g['home'])}", res) for b, res in g["bets"]]
            rows.append({
                **scoreboard(g["away"], g["home"], g["away_goals"], g["home_goals"], digits=0),
                "sep": g["end"] if g["end"] in ("OT", "SO") else "–",
                "proj": None if pa is None or ph is None else f"{pa:.1f} – {ph:.1f}",
                "pick": None if g["pick"] is None else graded_cell(f"{g['pick']} {g['pick_p']:.0%}",
                                                                   g["pick_res"]),
                "total": None if err is None else {"v": f"{g['expected_total']:.2f} → {g['total']}",
                                                   "sort": round(abs(err), 4)},
                **{lean_key[line]: graded_cell(*g["leans"][line]) for line in g["leans"]},
                **({"bet": bet_cells[0]} if bet_cells else {}),
                "flagged": bool(g["bets"]),
            })
        line = [f"{r['n']} games", f"straight up {_rec(r['su'])}", f"log loss {r['log_loss']:.3f}"]
        if r["mae"] is not None:
            line.append(f"totals MAE {r['mae']:.2f}")
        slates.append(Slate(day, _day_label(day), {
            "subtitle": f"{_day_label(day)}, {_season_label(season)} — " + " · ".join(line),
            "updated": updated,
            "search": "Search teams",
            **({"filters": [{**FLAGGED_FILTER, "label": "★ bets only"}]}
               if any(row["flagged"] for row in rows) else {}),
            "columns": game_cols,
            "rows": rows,
        }))
        by_day.append({
            "day": {"v": _day_label(day), "sort": day},
            "games": r["n"],
            "su": _vs_half(r["su"], r["accuracy"]),
            "logLoss": _lower_better(r["log_loss"], "log_loss"),
            "brier": _lower_better(r["brier"], "brier"),
            "mae": None if r["mae"] is None else {"v": f"{r['mae']:.2f}", "sort": round(r["mae"], 4)},
            **{lean_key[line]: _vs_half(r["leans"][line]) for line in LEAN_LINES},
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
            {"key": "mae", "label": "Totals MAE", "align": "right", "sortable": True},
            *({"key": lean_key[line], "label": f"O/U {line:g}", "align": "right", "sortable": True}
              for line in LEAN_LINES),
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
    ap.add_argument("--model", type=Path, default=MODEL, help="the NHL model's repo")
    ap.add_argument("--site-dir", type=Path, default=SITE_DATA / "nhl", help="site public/data/nhl dir")
    args = ap.parse_args()
    outputs = args.model / "outputs"

    days = {m.group("day"): p for p in (outputs / "daily").glob("*_predictions.csv")
            if (m := DAILY.match(p.name))}
    if not days:
        raise SystemExit(f"No daily predictions in {outputs / 'daily'}")
    frames = {day: pd.read_csv(p) for day, p in days.items()}
    # The season of a day is its games' season: game ids start with the first year (2026020017).
    seasons = {day: int(str(f["game_id"].iloc[0])[:4]) for day, f in frames.items() if len(f)}
    season = max(seasons.values())
    publish = sorted(day for day, s in seasons.items() if s == season)
    opens = args.day if args.day in publish else publish[-1]

    print(f"Exporting NHL {_season_label(season)} (opens on {opens})\n  from: {outputs}\n  to:   {args.site_dir}")
    names = goalie_names(args.model, pd.concat(frames[d]["game_id"] for d in publish).unique())
    publish_slates(
        args.site_dir / "games",
        [Slate(day, _day_label(day), build_day(days[day], day, names, season)) for day in publish],
        season=season,
        label=f"{_season_label(season)} Season",
        unit="day",
        latest=opens,
    )
    sources = [days[day] for day in publish]

    sim = outputs / "season_sim.csv"
    cup_favorite = None
    if sim.exists():
        write_table(args.site_dir / "season.json", build_season(sim, season))
        sources.append(sim)
        top = pd.read_csv(sim).sort_values("series_4", ascending=False).iloc[0]
        cup_favorite = {"label": "Cup favorite", "value": text(top["abbrev"]),
                        "detail": f"{num(top['series_4'], 4):.1%} Cup odds"}
    else:
        print(f"  [skip] no {sim.name}")

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

    finals, bets = read_results(args.model, season)
    graded = graded_games(frames, publish, finals, bets)
    record = None
    if graded:
        db = args.model / "data" / "nhl.sqlite"
        record = publish_results(args.site_dir / "results", graded, season=season,
                                 updated=file_date(db), backtest=_backtest(args.model))
        sources.append(db)
    else:
        print("  [skip] no finished game with a published prediction yet")

    # The card leads with the model's record once games are graded, else its #1 team.
    straight_up = None if record is None else {
        "label": "Straight up", "value": _rec(record["su"]), "detail": f"{record['accuracy']:.1%}",
        **({"tone": "good" if record["accuracy"] > 0.5 else "bad"} if record["accuracy"] != 0.5 else {})}
    write_card(args.site_dir, headline=_day_label(opens),
               updated=max(file_date(p) for p in sources),
               stats=[s for s in (straight_up or top_power, cup_favorite) if s])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
