#!/usr/bin/env python
"""NHL adapter: NHL_Projection_Model's outputs -> the site's JSON (via site_export).

Reads the model's outputs/ - plus, for goalie names, the NHL game-centre pages
it caches under data/raw/ - and writes public/data/nhl/:

    games/          one slate per DAY           outputs/daily/<date>_predictions.csv
                                                (+ <date>_bets.csv: flagged bets)
    season.json     standings + playoff odds    outputs/season_sim.csv
    rankings.json   power rankings              outputs/power_rankings.csv
    card.json       the landing card's line

scripts/update-data.ps1 runs this with the model's own venv Python (pandas):

    <model>\\.venv\\Scripts\\python.exe scripts\\exporters\\nhl.py [--day 2026-10-02]
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_export import (  # noqa: E402  (the site's shared export toolkit)
    FLAGGED_FILTER, SITE_DATA, Slate, file_date, num, publish_slates, scoreboard,
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

    write_card(args.site_dir, headline=_day_label(opens),
               updated=max(file_date(p) for p in sources),
               stats=[s for s in (cup_favorite, top_power) if s])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
