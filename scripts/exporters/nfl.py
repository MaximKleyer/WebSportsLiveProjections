#!/usr/bin/env python
"""NFL adapter: nfl_projector_v1's CSVs -> the site's JSON (via site_export).

Reads the model's output dir (data/processed/v2) and writes public/data/nfl/:

    games/        one slate per projected week     predictions_<season>_weekNN.csv
    season.json   standings, grouped by division   season_standings_<season>.csv
    results/      graded picks + season record     results_log.csv (from `grade`)

scripts/update-data.ps1 runs this with the model's own venv Python (pandas):

    <model>\\.venv\\Scripts\\python.exe scripts\\exporters\\nfl.py [--season 2026] [--week 3]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_export import (  # noqa: E402  (the site's shared export toolkit)
    SITE_DATA, Slate, file_date, flag, index_files, num, pick_weeks, publish_results,
    publish_slates, scoreboard, scoreboard_columns, spread, text, week_id, write_table,
)

MODEL_OUT = Path(r"C:\Users\maxim\NFLProjectionModel\nfl_projector_v1\data\processed\v2")

SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
PLAYER_LIMIT = 50


def build_games(path: Path, season: int, week: int) -> dict:
    df = pd.read_csv(path)
    has_vegas = "ats_pick" in df.columns and df["ats_pick"].notna().any()

    rows = []
    for d in df.to_dict("records"):
        rows.append({
            **scoreboard(d.get("away_team"), d.get("home_team"),
                         d.get("predicted_away_score"), d.get("predicted_home_score")),
            "homeWin": num(d.get("win_prob_home"), 4),
            "total": num(d.get("predicted_total")),
            "su": text(d.get("su_pick")),
            "ats": text(d.get("ats_pick")),
            "ou": text(d.get("ou_pick")),
        })

    columns = scoreboard_columns() + [
        {"key": "homeWin", "label": "Home Win", "align": "right", "format": "percent",
         "sortable": True},
        {"key": "total", "label": "Proj. Total", "align": "right", "format": "number",
         "sortable": True},
        {"key": "su", "label": "SU Pick", "align": "right"},
    ]
    if has_vegas:
        columns += [
            {"key": "ats", "label": "ATS", "align": "right"},
            {"key": "ou", "label": "O/U", "align": "right"},
        ]

    return {
        "subtitle": f"{season} · Week {week} — model projections",
        "updated": file_date(path),
        "columns": columns,
        "rows": rows,
    }


def build_players(path: Path, season: int, week: int) -> dict:
    # Unwired since the players view left the site (Sep 2026); kept for re-enable.
    df = pd.read_csv(path)
    df = df[df["position"].isin(SKILL_POSITIONS)].copy()

    for c in ("pass_yds", "rush_yds", "rec_yds", "receptions"):
        if c not in df.columns:
            df[c] = pd.NA
    df["_yds"] = (
        df[["pass_yds", "rush_yds", "rec_yds"]]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
        .sum(axis=1)
    )
    df = df.sort_values("_yds", ascending=False).head(PLAYER_LIMIT)

    rows = []
    for d in df.to_dict("records"):
        rows.append({
            "player": text(d.get("player")),
            "pos": text(d.get("position")),
            "team": text(d.get("team")),
            "opp": text(d.get("opponent")),
            "passYds": num(d.get("pass_yds")),
            "rushYds": num(d.get("rush_yds")),
            "rec": num(d.get("receptions")),
            "recYds": num(d.get("rec_yds")),
        })

    columns = [
        {"key": "player", "label": "Player"},
        {"key": "pos", "label": "Pos", "align": "center"},
        {"key": "team", "label": "Team", "align": "center"},
        {"key": "opp", "label": "Opp", "align": "center"},
        {"key": "passYds", "label": "Pass Yds", "align": "right", "format": "number", "sortable": True},
        {"key": "rushYds", "label": "Rush Yds", "align": "right", "format": "number", "sortable": True},
        {"key": "rec", "label": "Rec", "align": "right", "format": "number", "sortable": True},
        {"key": "recYds", "label": "Rec Yds", "align": "right", "format": "number", "sortable": True},
    ]

    return {
        "subtitle": f"{season} · Week {week} — top {len(rows)} skill players by projected yards",
        "updated": file_date(path),
        "columns": columns,
        "rows": rows,
    }


def build_season(path: Path, season: int) -> dict:
    # Grouped by division on the site: divisions A→Z, best record first within.
    df = pd.read_csv(path).sort_values(["division", "exp_wins"], ascending=[True, False])

    rows = []
    for d in df.to_dict("records"):
        wins, losses = num(d.get("exp_wins")), num(d.get("exp_losses"))
        rows.append({
            "team": text(d.get("team")),
            "division": text(d.get("division")),
            "record": {"v": f"{wins}–{losses}", "sort": wins},
            "playoff": num((d.get("playoff_pct") or 0) / 100, 4),
            "div": num((d.get("div_title_pct") or 0) / 100, 4),
        })

    # "division" stays on the rows as the group key (groupBy); the site
    # renders one sub-table per division instead of a Division column.
    columns = [
        {"key": "team", "label": "Team"},
        {"key": "record", "label": "Proj. Record", "align": "right", "sortable": True},
        {"key": "playoff", "label": "Playoff %", "align": "right", "format": "percent",
         "sortable": True},
        {"key": "div", "label": "Div Title %", "align": "right", "format": "percent",
         "sortable": True},
    ]

    return {
        "subtitle": f"{season} projected final standings — preseason simulation",
        "updated": file_date(path),
        "groupBy": "division",
        "columns": columns,
        "rows": rows,
    }


def graded_games(log: pd.DataFrame) -> list[dict]:
    """results_log rows -> site_export's graded-game shape.

    The model's grader already scored every pick (W/L/P, T for a tie), so this
    only builds display labels. spread_close is the home line (negative = home
    favoured), as grade.py uses it.
    """
    games = []
    for d in log.to_dict("records"):
        home = text(d.get("home_team"))
        line, total = num(d.get("spread_close")), num(d.get("total_close"))
        ats_pick, ou_pick = text(d.get("ats_pick")), text(d.get("ou_pick"))
        err = num(d.get("margin_error"), 4)
        games.append({
            "week": int(d["week"]),
            "order": text(d.get("kickoff")),
            "away": d.get("away_team"),
            "home": home,
            "away_score": d.get("actual_away"),
            "home_score": d.get("actual_home"),
            "proj_away": d.get("pred_away"),
            "proj_home": d.get("pred_home"),
            "su_pick": d.get("su_pick"),
            "su_res": d.get("su_result"),
            "ats_label": (f"{ats_pick} {spread(line if ats_pick == home else -line)}"
                          if ats_pick and line is not None else None),
            "ats_res": d.get("ats_result"),
            "ats_play": flag(d.get("spread_play")),
            "ou_label": f"{ou_pick} {total:g}" if ou_pick and total is not None else None,
            "ou_res": d.get("ou_result"),
            "ou_play": flag(d.get("total_play")),
            "abs_err": None if err is None else abs(err),
        })
    return games


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, help="season to export (default: latest)")
    ap.add_argument("--week", type=int, help="week the site opens on (default: latest projected)")
    ap.add_argument("--model-out", type=Path, default=MODEL_OUT, help="model output dir")
    ap.add_argument("--site-dir", type=Path, default=SITE_DATA / "nfl", help="site public/data/nfl dir")
    args = ap.parse_args()

    games = index_files(r"predictions_(?P<season>\d{4})_week(?P<week>\d{2})\.csv$", args.model_out)
    if not games:
        raise SystemExit(f"No predictions_*.csv found in {args.model_out}")
    season = args.season if args.season is not None else max(games)
    if season not in games:
        raise SystemExit(f"No predictions for season {season} (have {sorted(games)})")
    week, publish = pick_weeks(games[season], args.week)
    opens = week if week in publish else publish[-1]

    print(f"Exporting NFL {season} (opens on week {opens})\n  from: {args.model_out}\n  to:   {args.site_dir}")
    publish_slates(
        args.site_dir / "games",
        [Slate(week_id(season, wk), f"Week {wk}", build_games(games[season][wk], season, wk))
         for wk in publish],
        season=season,
        latest=week_id(season, opens),
    )

    standings = args.model_out / f"season_standings_{season}.csv"
    if standings.exists():
        write_table(args.site_dir / "season.json", build_season(standings, season))
    else:
        print(f"  [skip] no {standings.name}")

    log_path = args.model_out / "results_log.csv"
    log = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame()
    if "season" in log:
        log = log[log["season"] == season]
    if len(log):
        publish_results(args.site_dir / "results", graded_games(log),
                        season=season, updated=file_date(log_path))
    else:
        print(f"  [skip] no graded {season} games in results_log.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
