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

MODEL_OUT = Path(r"C:\Users\maxim\WebSportsLiveProjections\models\cfb\output")
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
            **({"id": str(int(d["game_id"]))} if num(d.get("game_id"), 0) is not None else {}),  # opens its card
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


# --------------------------------------------------------------------------
# game cards: one game's detail, opened from its row
# --------------------------------------------------------------------------
# market_margin and pred_margin are the home side's expected margin (market's
# and the model's); a *_edge is the model's number minus the market's, *_ev the
# expected return of a unit on the pick, *_play the model's flag.
def _pct(p) -> str:
    return "—" if p is None else f"{p:.0%}"


def _fair_odds(p):
    """A win probability as American odds without a margin: 0.54 -> '-117'."""
    if p is None or not 0 < p < 1:
        return None
    return f"{(-100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p):+.0f}"


def _ranked(name: str, rank) -> str:
    r = num(rank, 0)
    return f"No. {r:.0f} {name}" if r else name


def game_card(d: dict, records: dict, week: int, current: bool) -> dict:
    """The card for one projected game (a row of projections_*_week_N.csv). `current`: the latest
    projected week, whose card can show the season projections as they are now."""
    home, away = text(d.get("home_team")), text(d.get("away_team"))
    hp, ap = num(d.get("home_proj"), 1), num(d.get("away_proj"), 1)
    p_home = num(d.get("win_prob"), 4)
    p_away = None if p_home is None else round(1 - p_home, 4)
    pm, mm = num(d.get("pred_margin"), 2), num(d.get("market_margin"), 2)
    pt, mt = num(d.get("pred_total"), 2), num(d.get("market_total"), 2)
    neutral = flag(d.get("neutral_site"))

    def team(name, side):
        r = records.get(name, {}) if current else {}
        rank = num(d.get(f"{side}_rank"), 0)
        parts = [f"No. {rank:.0f}" if rank else None]
        if r:
            parts.append(f"{int(num(r.get('played_w'), 0) or 0)}–{int(num(r.get('played_l'), 0) or 0)}")
            parts.append(text(r.get("conference")))
        note = " · ".join(x for x in parts if x)
        return {"name": name, **({"note": note} if note else {})}

    def side_of(pick):
        return {"HOME": home, "AWAY": away}.get((text(pick) or "").upper())

    notes = []
    if hp is not None and ap is not None and p_home is not None:
        fav, p_fav = (home, p_home) if p_home >= 0.5 else (away, p_away)
        lead = "even" if abs(hp - ap) < 0.05 else f"{home if hp > ap else away} by {abs(hp - ap):.1f}"
        notes.append(f"The model projects {away} {ap:.1f} – {home} {hp:.1f}: {lead}"
                     + (" on a neutral field" if neutral else "") + f". That makes {fav} {p_fav:.0%} to win.")
    if mm is not None and pm is not None:
        market = "a pick'em" if abs(mm) < 0.05 else f"{home if mm > 0 else away} by {abs(mm):.1f}"
        model = "a pick'em" if abs(pm) < 0.05 else f"{home if pm > 0 else away} by {abs(pm):.1f}"
        line = f"The market expects {market}, the model {model}."
        pick, prob, ev = side_of(d.get("spread_pick")), num(d.get("spread_pick_prob"), 4), num(d.get("spread_ev"), 2)
        if pick and prob is not None:
            points = -mm if pick == home else mm  # the pick's own line (a consensus: not always a half point)
            line += (f" Against the spread it takes {pick} {spread(points)} ({prob:.0%} to cover"
                     + (f", expected return {ev:+.2f} a unit" if ev is not None else "") + ")"
                     + (" — a flagged play." if flag(d.get("spread_play")) else "."))
        notes.append(line)
    if mt is not None and pt is not None:
        pick, prob, ev = text(d.get("total_pick")), num(d.get("total_pick_prob"), 4), num(d.get("total_ev"), 2)
        line = f"The market's total is {mt:g} against the model's {pt:.1f}"
        if pick and prob is not None:
            line += (f": the {pick.lower()} ({prob:.0%}" + (f", expected return {ev:+.2f}" if ev is not None else "")
                     + ")" + (" — a flagged play." if flag(d.get("total_play")) else "."))
        notes.append(line if line.endswith(".") else line + ".")
    books, spread_books, move = num(d.get("n_books"), 0), num(d.get("book_dispersion"), 2), num(d.get("line_movement"), 2)
    if books:
        notes.append(f"Market: {books:.0f} book{'s' * (books != 1)}"
                     + (f", {spread_books:g} points apart" if spread_books else ", in agreement")
                     + (f"; the line has moved {move:+g} since it opened." if move else "; the line hasn't moved."))

    sections = []
    if notes:
        sections.append({"title": "What drives the projection", "kind": "notes", "items": notes})
    def gap(value, play):
        """The model-vs-market gap, toned when the model flags a play on it."""
        return {"v": f"{value:+.1f} ★", "tone": "good"} if flag(play) else f"{value:+.1f}"

    vs = []
    if pm is not None and mm is not None:
        vs.append([f"{home} margin", f"{pm:+.1f}", f"{mm:+.2f}".rstrip("0").rstrip("."), gap(pm - mm, d.get("spread_play"))])
    if pt is not None and mt is not None:
        vs.append(["Total", f"{pt:.1f}", f"{mt:g}", gap(pt - mt, d.get("total_play"))])
    if vs:
        sections.append({"title": "Model vs market", "kind": "table", "columns": ["", "Model", "Market", "Gap"],
                         "rows": vs, "note": "Margins are the home side's: + means a home win by that many."})
    probs = [[f"{away} to win", _pct(p_away)], [f"{home} to win", _pct(p_home)]]
    pick, prob = side_of(d.get("spread_pick")), num(d.get("spread_pick_prob"), 4)
    if pick and prob is not None and mm is not None:
        probs.append([f"{'★ ' if flag(d.get('spread_play')) else ''}{pick} {spread(-mm if pick == home else mm)}",
                      _pct(prob)])
    tpick, tprob = text(d.get("total_pick")), num(d.get("total_pick_prob"), 4)
    if tpick and tprob is not None and mt is not None:
        probs.append([f"{'★ ' if flag(d.get('total_play')) else ''}{tpick.title()} {mt:g}", _pct(tprob)])
    sections.append({"title": "Probabilities", "kind": "table", "columns": ["", "Model"], "rows": probs})

    ra, rh = (records.get(away, {}), records.get(home, {})) if current else ({}, {})
    if ra and rh:
        outlook = []
        for label, key, is_pct in (("Proj. wins", "exp_w", False), ("Conf. wins", "conf_exp_w", False),
                                   ("Bowl", "p_bowl", True)):
            x, y = num(ra.get(key), 3), num(rh.get(key), 3)
            if x is None or y is None:
                continue
            outlook.append({"label": label, "away": x, "home": y, "awayText": _pct(x) if is_pct else f"{x:.1f}",
                            "homeText": _pct(y) if is_pct else f"{y:.1f}", **({"max": 1} if is_pct else {})})
        if outlook:
            sections.append({"title": "Season outlook", "kind": "compare", "rows": outlook,
                             "note": "From the season simulation of the remaining schedule."})

    kick = _kickoff(d.get("start_date"))
    return {
        "title": f"{_ranked(away, d.get('away_rank'))} {'vs' if neutral else '@'} {_ranked(home, d.get('home_rank'))}",
        "subtitle": " · ".join(x for x in (f"Week {week}", kick and f"{kick['v']} ET", neutral and "neutral site") if x),
        "away": team(away, "away"),
        "home": team(home, "home"),
        **({"score": {"away": f"{ap:.1f}", "home": f"{hp:.1f}", "label": "Projected score"}}
           if hp is not None and ap is not None else {}),
        **({"win": {"away": p_away, "home": p_home,
                    "note": f"Fair odds: {away} {_fair_odds(p_away)}, {home} {_fair_odds(p_home)} (no margin)."}}
           if p_home is not None else {}),
        "sections": sections,
    }


def build_cards(path: Path, records: dict, week: int, current: bool) -> dict[str, dict]:
    """Cards for the week's games, by game id (their rows' ids)."""
    return {str(int(d["game_id"])): game_card(d, records, week, current)
            for d in pd.read_csv(path).to_dict("records") if num(d.get("game_id"), 0) is not None}


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
    records_path = args.model_out / f"season_{season}_projected_records.csv"
    by_team = ({text(r["team"]): r for r in pd.read_csv(records_path).to_dict("records")}
               if records_path.exists() else {})
    publish_slates(
        args.site_dir / "games",
        [Slate(week_id(season, wk), f"Week {wk}", build_games(games[season][wk], season, wk),
               build_cards(games[season][wk], by_team, wk, current=wk == max(publish)) or None)
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
        scheduled = {wk: len(pd.read_csv(p)) for wk, p in games[season].items()}  # games projected
        record = publish_results(args.site_dir / "results",
                                 graded_games(log, _kickoffs(args.model_out, season)),
                                 season=season, updated=file_date(log_path), search=SEARCH,
                                 scheduled=scheduled)
        sources.append(log_path)
    else:
        print(f"  [skip] no graded {season} games in results_log.csv")

    write_card(args.site_dir, headline=f"Week {opens}",
               updated=max(file_date(p) for p in sources),
               stats=card_stats(record) if record else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
