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
    SITE_DATA, Slate, card_stats, file_date, flag, index_files, num, pick_weeks,
    publish_results, publish_slates, scoreboard, scoreboard_columns, spread, text, week_id,
    write_card, write_table,
)

MODEL_OUT = Path(r"C:\Users\maxim\WebSportsLiveProjections\models\nfl\data\processed\v2")

SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
PLAYER_LIMIT = 50


def build_games(path: Path, season: int, week: int) -> dict:
    df = pd.read_csv(path)
    has_vegas = "ats_pick" in df.columns and df["ats_pick"].notna().any()

    rows = []
    for d in df.to_dict("records"):
        rows.append({
            "id": f"{text(d.get('away_team'))}@{text(d.get('home_team'))}",  # opens the game's card
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


# --------------------------------------------------------------------------
# game cards: one game's detail, opened from its row
# --------------------------------------------------------------------------
# The model is bottom-up: it projects each player's stat line, sums them into
# team production and derives the score from it, so a card shows that
# production side by side, the key players' lines (player_projections_*.csv)
# and the model's picks against the market.
TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams", "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def _pct(p) -> str:
    return "—" if p is None else f"{p:.0%}"


def _fair_odds(p):
    """A win probability as American odds without a margin: 0.54 -> '-117'."""
    if p is None or not 0 < p < 1:
        return None
    return f"{(-100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p):+.0f}"


def _sum(players: pd.DataFrame, col: str) -> float:
    return float(pd.to_numeric(players[col], errors="coerce").fillna(0).sum()) if col in players else 0.0


def _offense(players: pd.DataFrame) -> dict[str, float]:
    """A team's projected production: its players' lines added up."""
    return {"pass": _sum(players, "pass_yds"), "rush": _sum(players, "rush_yds") + _sum(players, "scramble_yds"),
            "att": _sum(players, "pass_att"), "car": _sum(players, "carries"), "sacks": _sum(players, "sacks"),
            "ints": _sum(players, "interceptions")}


def _key_players(team: str, players: pd.DataFrame) -> list[list]:
    """The team's starting QB, top two backs and top three receivers, each with his line."""
    rows = []
    for pos, sort, n in (("QB", "pass_yds", 1), ("RB", "rush_yds", 2)):
        group = players[players["position"] == pos].copy()
        group["_k"] = pd.to_numeric(group[sort], errors="coerce").fillna(0)
        for d in group.sort_values("_k", ascending=False).head(n).to_dict("records"):
            if pos == "QB":
                line = (f"{num(d.get('pass_yds'), 0) or 0:.0f} pass yds · {num(d.get('completions'), 0) or 0:.0f}/"
                        f"{num(d.get('pass_att'), 0) or 0:.0f} · {num(d.get('interceptions'), 1) or 0:.1f} INT")
            else:
                line = f"{num(d.get('rush_yds'), 0) or 0:.0f} rush yds on {num(d.get('carries'), 1) or 0:.1f} carries"
                if num(d.get("receptions"), 1):
                    line += f" · {num(d.get('receptions'), 1):.1f} rec, {num(d.get('rec_yds'), 0) or 0:.0f} yds"
            rows.append([team, text(d.get("player")), pos, line])
    catchers = players[players["position"].isin(["WR", "TE"])].copy()
    catchers["_k"] = pd.to_numeric(catchers["rec_yds"], errors="coerce").fillna(0)
    for d in catchers.sort_values("_k", ascending=False).head(3).to_dict("records"):
        share = num(d.get("target_share_pct"), 0)
        rows.append([team, text(d.get("player")), text(d.get("position")),
                     f"{num(d.get('receptions'), 1) or 0:.1f} rec, {num(d.get('rec_yds'), 0) or 0:.0f} yds"
                     + (f" · {share:.0f}% of targets" if share else "")])
    return rows


def game_card(d: dict, players: pd.DataFrame, standings: dict, week: int, current: bool) -> dict:
    """The card for one projected game (a row of predictions_*_weekNN.csv). `current`: the latest
    projected week, whose card can show the season standings as they are now."""
    away, home = text(d.get("away_team")), text(d.get("home_team"))
    pa, ph = num(d.get("predicted_away_score"), 1), num(d.get("predicted_home_score"), 1)
    wa, wh = num(d.get("win_prob_away"), 4), num(d.get("win_prob_home"), 4)
    total = num(d.get("predicted_total"), 1)
    game = players[players["game"] == f"{away}@{home}"] if "game" in players else players.iloc[0:0]
    team_players = {t: game[game["team"] == t] for t in (away, home)}
    off = {t: _offense(p) for t, p in team_players.items()}

    def team(abbr):
        s = standings.get(abbr, {}) if current else {}
        proj = (f"Proj. {num(s.get('exp_wins'), 1):.1f}–{num(s.get('exp_losses'), 1):.1f}"
                if num(s.get("exp_wins"), 1) is not None else None)
        return {"name": TEAM_NAMES.get(abbr, abbr), "abbr": abbr, **({"note": proj} if proj else {})}

    notes = []
    if pa is not None and ph is not None and wh is not None:
        fav, p_fav = (home, wh) if wh >= 0.5 else (away, wa if wa is not None else 1 - wh)
        lead = "even" if abs(ph - pa) < 0.05 else f"{home if ph > pa else away} by {abs(ph - pa):.1f}"
        notes.append(f"The model projects {away} {pa:.1f} – {home} {ph:.1f}: {lead}, {total or pa + ph:.1f} points "
                     f"in all. That makes {fav} {p_fav:.0%} to win.")
    sc, tc = num(d.get("spread_close")), num(d.get("total_close"))
    ats, ats_p, ou, ou_p = text(d.get("ats_pick")), num(d.get("ats_prob"), 4), text(d.get("ou_pick")), num(d.get("ou_prob"), 4)
    if sc is not None:
        parts = [f"The market line: {home} {spread(sc)}" + (f", total {tc:g}" if tc is not None else "") + "."]
        if ats and ats_p is not None:
            parts.append(f"Against the spread the model takes {ats} {spread(sc if ats == home else -sc)} ({ats_p:.0%} "
                         "to cover)")
        if ou and ou_p is not None and tc is not None:
            parts.append(f"on the total, the {ou.lower()} ({ou_p:.0%})")
        notes.append(parts[0] + (" " + "; ".join(parts[1:]) + "." if len(parts) > 1 else ""))
    angle, reason = text(d.get("situational_ats_pick")), text(d.get("situational_ats_reason"))
    if angle and reason:
        notes.append(f"Situational angle: {reason} — that favors {angle} against the spread.")
    qbs = {t: p[p["position"] == "QB"] for t, p in team_players.items()}
    if all(len(q) for q in qbs.values()):
        qa, qh = (q.sort_values("depth").iloc[0] for q in (qbs[away], qbs[home]))
        notes.append(f"Under center: {text(qa['player'])} projects {num(qa['pass_yds'], 0) or 0:.0f} passing yards "
                     f"for {away}, {text(qh['player'])} {num(qh['pass_yds'], 0) or 0:.0f} for {home}.")
    if len(game):
        notes.append(f"On the ground: {away} {off[away]['rush']:.0f} yards on {off[away]['car']:.0f} carries, "
                     f"{home} {off[home]['rush']:.0f} on {off[home]['car']:.0f}.")

    sections = []
    if notes:
        sections.append({"title": "What drives the projection", "kind": "notes", "items": notes})
    if len(game):
        rows = []
        for label, key, lower in (("Passing yards", "pass", False), ("Rushing yards", "rush", False),
                                  ("Pass attempts", "att", False), ("Carries", "car", False),
                                  ("Sacks taken", "sacks", True), ("Interceptions", "ints", True)):
            x, y = off[away][key], off[home][key]
            digits = 1 if key in ("sacks", "ints") else 0
            rows.append({"label": label, "away": round(x, 2), "home": round(y, 2), "awayText": f"{x:.{digits}f}",
                         "homeText": f"{y:.{digits}f}", **({"lower": True} if lower else {})})
        rows.insert(2, {"label": "Total yards", "away": round(off[away]["pass"] + off[away]["rush"], 1),
                        "home": round(off[home]["pass"] + off[home]["rush"], 1),
                        "awayText": f"{off[away]['pass'] + off[away]['rush']:.0f}",
                        "homeText": f"{off[home]['pass'] + off[home]['rush']:.0f}"})
        sections.append({"title": "Projected offense", "kind": "compare", "rows": rows,
                         "note": "The players' projected lines added up: rushing includes the quarterback's scrambles."})
        key = _key_players(away, team_players[away]) + _key_players(home, team_players[home])
        if key:
            sections.append({"title": "Key players", "kind": "table", "columns": ["Team", "Player", "Pos", "Projection"],
                             "rows": key})

    probs = [[f"{away} to win", _pct(wa)], [f"{home} to win", _pct(wh)]]
    if ats and ats_p is not None and sc is not None:
        probs.append([f"{ats} {spread(sc if ats == home else -sc)}", _pct(ats_p)])
    if ou and ou_p is not None and tc is not None:
        probs.append([f"{ou.title()} {tc:g}", _pct(ou_p)])
    sections.append({"title": "Probabilities", "kind": "table", "columns": ["", "Model"], "rows": probs})

    if current and standings.get(away) and standings.get(home):
        outlook = []
        for label, key, scale in (("Proj. wins", "exp_wins", 1), ("Playoffs", "playoff_pct", 100),
                                  ("Division", "div_title_pct", 100)):
            x, y = num(standings[away].get(key), 4), num(standings[home].get(key), 4)
            if x is None or y is None:
                continue
            x, y = x / scale, y / scale
            outlook.append({"label": label, "away": x, "home": y,
                            "awayText": f"{x:.1f}" if scale == 1 else _pct(x),
                            "homeText": f"{y:.1f}" if scale == 1 else _pct(y), **({"max": 1} if scale != 1 else {})})
        if outlook:
            sections.append({"title": "Season outlook", "kind": "compare", "rows": outlook,
                             "note": "From the season simulation: 10,000 seasons from the latest projections."})

    return {
        "title": f"{away} @ {home}",
        "subtitle": f"Week {week}",
        "away": team(away),
        "home": team(home),
        **({"score": {"away": f"{pa:.1f}", "home": f"{ph:.1f}", "label": "Projected score"}}
           if pa is not None and ph is not None else {}),
        **({"win": {"away": wa if wa is not None else round(1 - wh, 4), "home": wh,
                    "note": f"Fair odds: {away} {_fair_odds(1 - wh)}, {home} {_fair_odds(wh)} (no margin)."}}
           if wh is not None else {}),
        "sections": sections,
    }


def build_cards(path: Path, players_path: Path, standings: dict, week: int, current: bool) -> dict[str, dict]:
    """Cards for the week's games, by '<away>@<home>' (their rows' ids)."""
    players = pd.read_csv(players_path) if players_path.exists() else pd.DataFrame(columns=["game", "team", "position"])
    return {f"{text(d.get('away_team'))}@{text(d.get('home_team'))}": game_card(d, players, standings, week, current)
            for d in pd.read_csv(path).to_dict("records")}


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
    standings_path = args.model_out / f"season_standings_{season}.csv"
    standings = ({text(r["team"]): r for r in pd.read_csv(standings_path).to_dict("records")}
                 if standings_path.exists() else {})
    publish_slates(
        args.site_dir / "games",
        [Slate(week_id(season, wk), f"Week {wk}", build_games(games[season][wk], season, wk),
               build_cards(games[season][wk], args.model_out / f"player_projections_{season}_week{wk:02d}.csv",
                           standings, wk, current=wk == max(publish)) or None)
         for wk in publish],
        season=season,
        latest=week_id(season, opens),
    )

    sources = [games[season][wk] for wk in publish]  # every model file this export read

    standings = args.model_out / f"season_standings_{season}.csv"
    if standings.exists():
        write_table(args.site_dir / "season.json", build_season(standings, season))
        sources.append(standings)
    else:
        print(f"  [skip] no {standings.name}")

    log_path = args.model_out / "results_log.csv"
    log = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame()
    if "season" in log:
        log = log[log["season"] == season]
    record = None
    if len(log):
        scheduled = {wk: len(pd.read_csv(p)) for wk, p in games[season].items()}  # games predicted
        record = publish_results(args.site_dir / "results", graded_games(log),
                                 season=season, updated=file_date(log_path), scheduled=scheduled)
        sources.append(log_path)
    else:
        print(f"  [skip] no graded {season} games in results_log.csv")

    write_card(args.site_dir, headline=f"Week {opens}",
               updated=max(file_date(p) for p in sources),
               stats=card_stats(record) if record else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
