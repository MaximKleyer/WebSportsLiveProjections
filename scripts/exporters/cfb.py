#!/usr/bin/env python
"""CFB adapter: CFB_Projection_Model's CSVs -> the site's JSON (via site_export).

Reads the model's output/ dir and writes public/data/cfb/:

    games/        one slate per projected week       projections_<season>_week_<N>.csv
    season.json   projected records, by conference   season_<season>_projected_records.csv
    results/      graded picks + season record       results_log.csv (from grade.py)

scripts/update-data.ps1 runs this with the model's own venv Python (pandas):

    <model>\\.venv\\Scripts\\python.exe scripts\\exporters\\cfb.py [--season 2026] [--week 4]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_export import (  # noqa: E402  (the site's shared export toolkit)
    FLAGGED_FILTER, SITE_DATA, Slate, card_stats, file_date, flag, index_files, num,
    pick_weeks, publish_results, publish_slates, scoreboard, scoreboard_columns, spread, text,
    week_id, write_card, write_table,
)

MODEL_OUT = Path(r"C:\Users\maxim\CFB_Projection_Model\output")
EASTERN = ZoneInfo("America/New_York")
SEARCH = "Search teams"


def _kickoff(x):
    """'2026-09-11 00:00:00+00:00' (UTC) -> a cell showing 'Thu 9/10 8:00PM'
    Eastern that sorts by the actual time."""
    if text(x) is None:
        return None
    try:
        ts = pd.to_datetime(x, utc=True)
    except (ValueError, TypeError):
        return None
    et = ts.tz_convert(EASTERN)
    hour12 = et.hour % 12 or 12
    ampm = "AM" if et.hour < 12 else "PM"
    return {"v": f"{et.strftime('%a')} {et.month}/{et.day} {hour12}:{et.minute:02d}{ampm}",
            "sort": ts.isoformat()}


def _pick_cell(pick, edge, play, *, home=None, away=None):
    """One display cell for a model pick - '★ Miami +7.4' / 'UNDER +4.5' -
    that sorts by the size of the edge.

    `pick` is HOME/AWAY (mapped to the team name) or OVER/UNDER (shown as-is);
    the edge is the model-vs-market gap in points (shown as absolute value);
    a leading star marks a flagged play (the model's recommended bets).
    """
    p = text(pick)
    if p is None:
        return None
    label = {"HOME": home, "AWAY": away}.get(p.upper(), p)
    if label is None:
        return None
    e = num(edge, 2)
    shown = label if e is None else f"{label} +{abs(e):.1f}"
    shown = f"★ {shown}" if flag(play) else shown
    return shown if e is None else {"v": shown, "sort": abs(e)}


def build_games(path: Path, season: int, week: int) -> dict:
    df = pd.read_csv(path)
    if "start_date" in df.columns:
        df = df.sort_values("start_date")

    rows = []
    for d in df.to_dict("records"):
        home, away = text(d.get("home_team")), text(d.get("away_team"))
        rows.append({
            "kickoff": _kickoff(d.get("start_date")),
            **scoreboard(away, home, d.get("away_proj"), d.get("home_proj")),
            "homeWin": num(d.get("win_prob"), 4),
            "total": num(d.get("pred_total")),
            "ats": _pick_cell(d.get("spread_pick"), d.get("spread_edge"),
                              d.get("spread_play"), home=home, away=away),
            "ou": _pick_cell(d.get("total_pick"), d.get("total_edge"), d.get("total_play")),
            "flagged": flag(d.get("spread_play")) or flag(d.get("total_play")),
        })

    columns = [{"key": "kickoff", "label": "Kickoff", "sortable": True}] + scoreboard_columns() + [
        {"key": "homeWin", "label": "Home Win", "align": "right", "format": "percent",
         "sortable": True},
        {"key": "total", "label": "Proj. Total", "align": "right", "format": "number",
         "sortable": True},
        {"key": "ats", "label": "ATS (edge)", "align": "right", "sortable": True},
        {"key": "ou", "label": "O/U (edge)", "align": "right", "sortable": True},
    ]

    return {
        "subtitle": f"{season} · Week {week} — kickoffs ET · ★ = flagged play",
        "updated": file_date(path),
        "search": SEARCH,
        **({"filters": [FLAGGED_FILTER]} if any(r["flagged"] for r in rows) else {}),
        "columns": columns,
        "rows": rows,
    }


def build_season(path: Path, season: int) -> dict:
    # Grouped by conference on the site: conferences A→Z, best record first within.
    df = pd.read_csv(path).sort_values(["conference", "exp_w"], ascending=[True, False])

    rows = []
    for d in df.to_dict("records"):
        pw, pl = num(d.get("played_w"), 0), num(d.get("played_l"), 0)
        wins, losses = num(d.get("exp_w")), num(d.get("exp_l"))
        rows.append({
            "team": text(d.get("team")),
            "conference": text(d.get("conference")),
            "current": None if pw is None or pl is None else f"{int(pw)}–{int(pl)}",
            "record": {"v": f"{wins}–{losses}", "sort": wins},
            "bowl": num(d.get("p_bowl"), 4),
        })

    # "conference" stays on the rows as the group key (groupBy); the site
    # renders one sub-table per conference instead of a Conference column.
    columns = [
        {"key": "team", "label": "Team"},
        {"key": "current", "label": "Current", "align": "right"},
        {"key": "record", "label": "Proj. Record", "align": "right", "sortable": True},
        {"key": "bowl", "label": "Bowl %", "align": "right", "format": "percent",
         "sortable": True},
    ]

    return {
        "subtitle": f"{season} projected final records — all FBS teams",
        "updated": file_date(path),
        "groupBy": "conference",
        "search": "Search teams or conferences",
        "columns": columns,
        "rows": rows,
    }


def _kickoffs(model_out: Path, season: int) -> dict:
    """{game_id: start_date} from the saved projections, to order each week."""
    starts: dict = {}
    for p in model_out.glob(f"projections_{season}_week_*.csv"):
        try:
            df = pd.read_csv(p, usecols=["game_id", "start_date"])
        except ValueError:  # an older CSV without start_date
            continue
        starts.update(zip(df["game_id"], df["start_date"].astype(str)))
    return starts


def graded_games(log: pd.DataFrame, starts: dict) -> list[dict]:
    """results_log rows -> site_export's graded-game shape.

    The CFB log keeps raw projections, lines and finals, so every pick is
    graded here with exactly the rules cfb_model.grade.score uses: the model
    takes the home side when pred_margin > market_margin (the market's
    expected home margin), the over when pred_total > market_total, and a
    result landing on the number is a push.
    """
    games = []
    for d in log.to_dict("records"):
        home, away = text(d.get("home_team")), text(d.get("away_team"))
        # 6 decimals is effectively unrounded - these feed exact line comparisons.
        pm, mm, am = (num(d.get(k), 6) for k in ("pred_margin", "market_margin", "actual_margin"))
        pt, mt, at = (num(d.get(k), 6) for k in ("pred_total", "market_total", "actual_total"))

        su_pick = home if (pm or 0) > 0 else away if (pm or 0) < 0 else None
        su_res = None
        if su_pick and am is not None:
            su_res = "T" if am == 0 else ("W" if (am > 0) == (pm > 0) else "L")

        ats_label = ats_res = None
        if pm is not None and mm is not None:
            picked_home = pm > mm
            ats_label = f"{home} {spread(-mm)}" if picked_home else f"{away} {spread(mm)}"
            if am is not None:
                ats_res = "P" if am == mm else ("W" if (am > mm) == picked_home else "L")

        ou_label = ou_res = None
        if pt is not None and mt is not None:
            picked_over = pt > mt
            ou_label = f"{'OVER' if picked_over else 'UNDER'} {mt:g}"
            if at is not None:
                ou_res = "P" if at == mt else ("W" if (at > mt) == picked_over else "L")

        games.append({
            "week": int(d["week"]),
            "order": starts.get(d.get("game_id")),
            "away": away,
            "home": home,
            "away_score": d.get("actual_away"),
            "home_score": d.get("actual_home"),
            "proj_away": d.get("away_proj"),
            "proj_home": d.get("home_proj"),
            "su_pick": su_pick,
            "su_res": su_res,
            "ats_label": ats_label,
            "ats_res": ats_res,
            "ats_play": flag(d.get("spread_play")),
            "ou_label": ou_label,
            "ou_res": ou_res,
            "ou_play": flag(d.get("total_play")),
            "abs_err": None if pm is None or am is None else abs(pm - am),
        })
    return games


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, help="season to export (default: latest)")
    ap.add_argument("--week", type=int, help="week the site opens on (default: latest projected)")
    ap.add_argument("--model-out", type=Path, default=MODEL_OUT, help="model output dir")
    ap.add_argument("--site-dir", type=Path, default=SITE_DATA / "cfb", help="site public/data/cfb dir")
    args = ap.parse_args()

    games = index_files(r"projections_(?P<season>\d{4})_week_(?P<week>\d+)\.csv$", args.model_out)
    if not games:
        raise SystemExit(f"No projections_*.csv found in {args.model_out}")
    season = args.season if args.season is not None else max(games)
    if season not in games:
        raise SystemExit(f"No projections for season {season} (have {sorted(games)})")
    week, publish = pick_weeks(games[season], args.week)
    opens = week if week in publish else publish[-1]

    print(f"Exporting CFB {season} (opens on week {opens})\n  from: {args.model_out}\n  to:   {args.site_dir}")
    publish_slates(
        args.site_dir / "games",
        [Slate(week_id(season, wk), f"Week {wk}", build_games(games[season][wk], season, wk))
         for wk in publish],
        season=season,
        latest=week_id(season, opens),
    )

    sources = [games[season][wk] for wk in publish]  # every model file this export read

    records = args.model_out / f"season_{season}_projected_records.csv"
    if records.exists():
        write_table(args.site_dir / "season.json", build_season(records, season))
        sources.append(records)
    else:
        print(f"  [skip] no {records.name}")

    log_path = args.model_out / "results_log.csv"
    log = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame()
    if "season" in log:
        log = log[log["season"] == season]
    record = None
    if len(log):
        record = publish_results(args.site_dir / "results",
                                 graded_games(log, _kickoffs(args.model_out, season)),
                                 season=season, updated=file_date(log_path), search=SEARCH)
        sources.append(log_path)
    else:
        print(f"  [skip] no graded {season} games in results_log.csv")

    write_card(args.site_dir, headline=f"Week {opens}",
               updated=max(file_date(p) for p in sources),
               stats=card_stats(record) if record else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
