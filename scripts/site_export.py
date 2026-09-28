#!/usr/bin/env python
"""Shared toolkit for the model exporters: the site's data contract in one place.

Each model has a thin adapter in scripts/exporters/ that reads its CSVs into
rows; this module builds, validates and writes the JSON the site renders.
Stdlib only, so CI can run the check below without the models' packages.

  table     { subtitle?, updated?, summary?, groupBy?, search?, filters?,
              columns: [{ key, label, align?, format?, sortable? }],
              rows: [{ <column key or row field>: cell }] }
            cell: a plain value (str / number / bool / null) or
                  { v, tone?: good|bad|muted, sort?: number|str }
  manifest  { season, label, unit, latest, summary?,
              slates: [{ id, label, file }] }      the index.json of a slate view
  summary   { title?, stats: [{ label, value, detail?, tone? }], note? }

A "slate" is one selectable table in a view: a football week, an MLB day.
Every write is validated, so a bad export fails here instead of on the site.

    python scripts/site_export.py check      # validate everything in public/data
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import NamedTuple

SITE_DATA = Path(__file__).resolve().parent.parent / "public" / "data"

ALIGNS = {"left", "right", "center"}
FORMATS = {"percent", "number"}
TONES = {"good", "bad", "muted"}

# A slate's files are written within days of each other; anything older than
# this relative to the newest file is history (or a stale test run).
RECENT_DAYS = 10


# --------------------------------------------------------------------------
# values: CSV / pandas values -> clean JSON values
# --------------------------------------------------------------------------
def _missing(x) -> bool:
    """None, NaN, or pandas' NA / NaT - without importing pandas."""
    if x is None:
        return True
    if isinstance(x, float):
        return math.isnan(x)
    return type(x).__name__ in ("NAType", "NaTType")


def num(x, digits=1):
    """Float rounded to `digits`, or None for blank/NaN/non-numeric."""
    if _missing(x):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def text(x):
    """Clean string, or None for blank/NaN. (pandas' str dtype stores missing
    values as NaN, not None - run every string through here.)"""
    if _missing(x):
        return None
    s = str(x).strip()
    return s or None


def flag(x) -> bool:
    """True for bool True or the string 'true' (any case)."""
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() == "true"


def spread(x: float) -> str:
    """A point spread from the picked side's view: +6.5 / -3 / PK."""
    return "PK" if x == 0 else f"{x:+g}"


def file_date(path: Path) -> str:
    """When a model wrote this file - the honest 'updated' for its table."""
    return date.fromtimestamp(path.stat().st_mtime).isoformat()


# --------------------------------------------------------------------------
# shared table pieces
# --------------------------------------------------------------------------
def scoreboard(away, home, away_score, home_score, *, digits: int = 1) -> dict:
    """Row cells for the mini-scoreboard: [Away] [score] [–] [score] [Home]."""
    a, h = num(away_score, digits), num(home_score, digits)
    return {
        "awayTeam": text(away),
        "awayScore": "" if a is None else f"{a:.{digits}f}",
        "sep": "–" if a is not None and h is not None else "",
        "homeScore": "" if h is None else f"{h:.{digits}f}",
        "homeTeam": text(home),
    }


def scoreboard_columns(label: str = "") -> list[dict]:
    """The five scoreboard columns; `label` heads the dash column ('Final')."""
    return [
        {"key": "awayTeam", "label": "Away", "align": "right"},
        {"key": "awayScore", "label": "", "align": "right"},
        {"key": "sep", "label": label, "align": "center"},
        {"key": "homeScore", "label": "", "align": "left"},
        {"key": "homeTeam", "label": "Home", "align": "left"},
    ]


FLAGGED_FILTER = {"field": "flagged", "label": "★ plays only"}


# --------------------------------------------------------------------------
# contract checks
# --------------------------------------------------------------------------
class ContractError(ValueError):
    """Data the site can't render as intended."""


def _check_scalar(v, where):
    if v is None or isinstance(v, (str, bool, int)):
        return
    if isinstance(v, float) and math.isfinite(v):
        return
    raise ContractError(f"{where}: {v!r} is not a JSON-safe value")


def _check_cell(cell, where):
    if not isinstance(cell, dict):
        _check_scalar(cell, where)
        return
    if "v" not in cell:
        raise ContractError(f"{where}: an object cell needs a 'v'")
    extra = set(cell) - {"v", "tone", "sort"}
    if extra:
        raise ContractError(f"{where}: unknown cell field(s) {sorted(extra)}")
    if cell.get("tone") is not None and cell["tone"] not in TONES:
        raise ContractError(f"{where}: tone {cell['tone']!r} is not one of {sorted(TONES)}")
    _check_scalar(cell["v"], where)
    if "sort" in cell:
        _check_scalar(cell["sort"], f"{where} (sort)")


def _check_str(obj: dict, key: str, where: str, required=False):
    value = obj.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str):
        raise ContractError(f"{where}: '{key}' must be a string")


def check_summary(s, where="summary"):
    stats = s.get("stats") if isinstance(s, dict) else None
    if not isinstance(stats, list) or not stats:
        raise ContractError(f"{where}: needs a non-empty 'stats' list")
    for key in ("title", "note"):
        _check_str(s, key, where)
    for i, stat in enumerate(stats):
        sw = f"{where} stat {i}"
        if not isinstance(stat, dict):
            raise ContractError(f"{sw}: not an object")
        _check_str(stat, "label", sw, required=True)
        _check_str(stat, "value", sw, required=True)
        _check_str(stat, "detail", sw)
        if stat.get("tone") is not None and stat["tone"] not in TONES:
            raise ContractError(f"{sw}: tone {stat['tone']!r} is not one of {sorted(TONES)}")


def check_table(t, where="table"):
    if not isinstance(t, dict):
        raise ContractError(f"{where}: not an object")
    cols = t.get("columns")
    if not isinstance(cols, list) or not cols:
        raise ContractError(f"{where}: needs a non-empty 'columns' list")
    keys: list[str] = []
    for i, c in enumerate(cols):
        cw = f"{where} column {i}"
        if not isinstance(c, dict):
            raise ContractError(f"{cw}: not an object")
        _check_str(c, "key", cw, required=True)
        _check_str(c, "label", cw, required=True)
        if c["key"] in keys:
            raise ContractError(f"{cw}: duplicate key {c['key']!r}")
        keys.append(c["key"])
        if c.get("align", "left") not in ALIGNS:
            raise ContractError(f"{cw}: align {c['align']!r} is not one of {sorted(ALIGNS)}")
        if c.get("format") is not None and c["format"] not in FORMATS:
            raise ContractError(f"{cw}: format {c['format']!r} is not one of {sorted(FORMATS)}")
        if not isinstance(c.get("sortable", False), bool):
            raise ContractError(f"{cw}: 'sortable' must be true/false")

    rows = t.get("rows")
    if not isinstance(rows, list):
        raise ContractError(f"{where}: needs a 'rows' list")
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ContractError(f"{where} row {i}: not an object")
        for k in keys:
            _check_cell(r.get(k), f"{where} row {i} [{k}]")

    group = t.get("groupBy")
    if group is not None:
        if not isinstance(group, str):
            raise ContractError(f"{where}: 'groupBy' must be a string")
        if rows and not any(r.get(group) is not None for r in rows):
            raise ContractError(f"{where}: groupBy {group!r} is not a field on any row")
    filters = t.get("filters", [])
    if not isinstance(filters, list):
        raise ContractError(f"{where}: 'filters' must be a list")
    for i, f in enumerate(filters):
        fw = f"{where} filter {i}"
        if not isinstance(f, dict):
            raise ContractError(f"{fw}: not an object")
        _check_str(f, "field", fw, required=True)
        _check_str(f, "label", fw, required=True)
    for key in ("subtitle", "updated", "search"):
        _check_str(t, key, where)
    if "summary" in t:
        check_summary(t["summary"], f"{where} summary")


def check_manifest(m, where="index.json", view_dir: Path | None = None):
    if not isinstance(m, dict):
        raise ContractError(f"{where}: not an object")
    slates = m.get("slates")
    if not isinstance(slates, list) or not slates:
        raise ContractError(f"{where}: needs a non-empty 'slates' list")
    files: list[str] = []
    for i, s in enumerate(slates):
        sw = f"{where} slate {i}"
        if not isinstance(s, dict):
            raise ContractError(f"{sw}: not an object")
        for key in ("id", "label", "file"):
            _check_str(s, key, sw, required=True)
        if s["file"] in files:
            raise ContractError(f"{sw}: duplicate file {s['file']!r}")
        files.append(s["file"])
        if view_dir is not None and not (view_dir / s["file"]).is_file():
            raise ContractError(f"{sw}: {s['file']} does not exist")
    if m.get("latest") not in files:
        raise ContractError(f"{where}: latest {m.get('latest')!r} is not one of its slates")
    for key in ("label", "unit"):
        _check_str(m, key, where)
    if "summary" in m:
        check_summary(m["summary"], f"{where} summary")


# --------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------
def _dump(path: Path, payload: dict):
    # allow_nan=False: a stray NaN fails loudly instead of emitting invalid JSON.
    path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")


def _rel(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def write_table(path: Path, table: dict):
    """Validate and write one table (a single-file view like season.json)."""
    check_table(table, _rel(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump(path, table)
    print(f"  wrote {_rel(path):24s} ({len(table['rows'])} rows)")


class Slate(NamedTuple):
    """One selectable table in a slate view: a football week, an MLB day."""

    id: str      # also the file name: '2026-w03' -> 2026-w03.json
    label: str   # what the dropdown shows: 'Week 3'
    table: dict


def publish_slates(view_dir: Path, slates: list[Slate], *, season: int, latest: str,
                   unit: str = "week", summary: dict | None = None):
    """Write each slate's table and the view's index.json (opening on slate
    `latest`), then delete any table the manifest no longer lists - the
    directory belongs to this view."""
    view_dir.mkdir(parents=True, exist_ok=True)
    for s in slates:
        write_table(view_dir / f"{s.id}.json", s.table)
    manifest = {
        "season": season,
        "label": f"{season} Season",
        "unit": unit,
        "latest": f"{latest}.json",
        **({"summary": summary} if summary else {}),
        "slates": [{"id": s.id, "label": s.label, "file": f"{s.id}.json"} for s in slates],
    }
    check_manifest(manifest, _rel(view_dir / "index.json"), view_dir)
    _dump(view_dir / "index.json", manifest)
    print(f"  wrote {_rel(view_dir / 'index.json'):24s} ({len(slates)} slates, opens on {latest})")

    keep = {"index.json"} | {f"{s.id}.json" for s in slates}
    for p in sorted(view_dir.glob("*.json")):
        if p.name not in keep:
            p.unlink()
            print(f"  removed {_rel(p)} (no longer published)")


# --------------------------------------------------------------------------
# football weeks
# --------------------------------------------------------------------------
def index_files(pattern: str, directory: Path) -> dict[int, dict[int, Path]]:
    """{season: {week: path}} for files whose name matches `pattern`, a regex
    with named groups 'season' and 'week'."""
    found: dict[int, dict[int, Path]] = {}
    rx = re.compile(pattern)
    for p in directory.glob("*.csv"):
        m = rx.match(p.name)
        if m:
            found.setdefault(int(m.group("season")), {})[int(m.group("week"))] = p
    return found


def pick_weeks(files: dict[int, Path], week: int | None) -> tuple[int, list[int]]:
    """Choose the week the site opens on, and which weeks to publish.

    Default: the highest week among the recently written files (within
    RECENT_DAYS of the newest), so re-running an old week doesn't steal the
    default and a months-old test run of a future week can't either.
    Published: every week up to the default, plus any later week that is
    itself recent - stale runs of future weeks stay off the site until the
    model re-projects them. An explicit `week` overrides the default.
    """
    mtimes = {w: p.stat().st_mtime for w, p in files.items()}
    newest = max(mtimes.values())
    recent = {w for w, t in mtimes.items() if newest - t <= RECENT_DAYS * 86400}
    default = week if week is not None else max(recent)
    return default, sorted(w for w in files if w <= default or w in recent)


def week_id(season: int, week: int) -> str:
    """Slate id (and file stem) for a football week: '2026-w03'."""
    return f"{season}-w{week:02d}"


# --------------------------------------------------------------------------
# results: graded picks
# --------------------------------------------------------------------------
# Each adapter turns its model's grading log into "graded games", one dict per
# game with these keys - the neutral shape publish_results reads:
#   week, order (sort key within the week, e.g. kickoff), away, home,
#   away_score, home_score, proj_away, proj_home,
#   su_pick, su_res, ats_label, ats_res, ats_play, ou_label, ou_res, ou_play,
#   abs_err (|projected margin - actual margin|)
# where *_res is 'W' / 'L' / 'P' (push) / 'T' (tie) / None (no pick or line).

BREAK_EVEN_110 = 110 / 210  # win rate needed to profit at -110
PAYOUT_110 = 100 / 110      # profit per unit staked at -110
GLYPH = {"W": "✓", "L": "✗", "P": "(push)", "T": "(tie)"}
RESULT_TONE = {"W": "good", "L": "bad", "P": "muted", "T": "muted"}
RESULT_SORT = {"W": 1, "P": 0.5, "T": 0.5, "L": 0}
RESULTS_NOTE = (
    "Graded against the line the model projected against, at -110 "
    "(break-even 52.4%). Win % excludes pushes. ★ = flagged play; "
    "units assume 1u per flagged play."
)


def _clean(g: dict) -> dict:
    """Normalise one graded game so NaNs from pandas can't leak into counts."""
    return {
        **g,
        **{k: text(g.get(k)) for k in ("away", "home", "su_pick", "su_res", "ats_label",
                                       "ats_res", "ou_label", "ou_res")},
        **{k: flag(g.get(k)) for k in ("ats_play", "ou_play")},
        "order": text(g.get("order")) or "",
        "abs_err": num(g.get("abs_err"), 4),
    }


def _tally(results, third: str = "P") -> tuple[int, int, int]:
    results = list(results)
    return results.count("W"), results.count("L"), results.count(third)


def _records(games: list[dict]) -> dict:
    """W/L/P tallies for every pick type in a set of graded games."""
    errs = [g["abs_err"] for g in games if g["abs_err"] is not None]
    return {
        "n": len(games),
        "su": _tally((g["su_res"] for g in games), third="T"),
        "ats": _tally(g["ats_res"] for g in games),
        "ou": _tally(g["ou_res"] for g in games),
        "spread_plays": _tally(g["ats_res"] for g in games if g["ats_play"]),
        "total_plays": _tally(g["ou_res"] for g in games if g["ou_play"]),
        "mae": num(sum(errs) / len(errs)) if errs else None,
    }


def _rec(t: tuple[int, int, int]) -> str:
    w, l, p = t
    return f"{w}–{l}" + (f"–{p}" if p else "")


def _rate_tone(t, judged=True):
    """(win rate excluding pushes, tone vs the -110 break-even)."""
    w, l, _ = t
    rate = w / (w + l) if (w + l) else None
    if not judged or rate is None or rate == BREAK_EVEN_110:
        return rate, None
    return rate, "good" if rate > BREAK_EVEN_110 else "bad"


def _stat(label, t, *, judged=True, units=False) -> dict:
    """One summary tile: record, win %, and units won for flagged plays."""
    rate, tone = _rate_tone(t, judged)
    detail = "—" if rate is None else f"{rate:.1%}"
    if units and rate is not None:
        detail += f" · {t[0] * PAYOUT_110 - t[1]:+.1f}u"
    return {"label": label, "value": _rec(t), "detail": detail, "tone": tone}


def _rec_cell(t, judged=True):
    """'8–7–1 (53%)' toned against break-even; None when nothing was picked."""
    if sum(t) == 0:
        return None
    rate, tone = _rate_tone(t, judged)
    if rate is None:  # only pushes
        return _rec(t)
    cell = {"v": f"{_rec(t)} ({rate:.0%})", "sort": round(rate, 4)}
    if tone:
        cell["tone"] = tone
    return cell


def graded_cell(label, result, play=False):
    """'★ ATL +6 ✓' toned by the result, or None when there was no pick."""
    label, result = text(label), text(result)
    if not label:
        return None
    shown = f"★ {label}" if play else label
    if result in GLYPH:
        return {"v": f"{shown} {GLYPH[result]}", "tone": RESULT_TONE[result],
                "sort": RESULT_SORT[result]}
    return shown


def publish_results(view_dir: Path, games: list[dict], *, season: int, updated: str,
                    search: str | None = None):
    """Write a results view: the season record (manifest summary tiles), an
    'All weeks' record-by-week table, and one slate per graded week."""
    if not games:
        raise ContractError(f"{view_dir}: no graded games to publish")
    games = sorted((_clean(g) for g in games), key=lambda g: (int(g["week"]), g["order"]))
    total = _records(games)
    summary = {
        "title": f"{season} season to date · {total['n']} games graded "
                 f"through Week {int(games[-1]['week'])}",
        "stats": [
            _stat("Straight up", total["su"], judged=False),
            _stat("Against the spread", total["ats"]),
            _stat("Over / under", total["ou"]),
            _stat("★ Spread plays", total["spread_plays"], units=True),
            _stat("★ Total plays", total["total_plays"], units=True),
            {"label": "Margin MAE",
             "value": "—" if total["mae"] is None else f"{total['mae']:.1f}",
             "detail": "avg miss, points"},
        ],
        "note": RESULTS_NOTE,
    }

    # Mini-scoreboard with FINAL scores, then the projection and each graded pick.
    game_cols = scoreboard_columns("Final") + [
        {"key": "proj", "label": "Proj.", "align": "right"},
        {"key": "su", "label": "SU Pick", "align": "right", "sortable": True},
        {"key": "ats", "label": "ATS", "align": "right", "sortable": True},
        {"key": "ou", "label": "O/U", "align": "right", "sortable": True},
    ]
    slates, by_week = [], []
    for wk, week_games in groupby(games, key=lambda g: int(g["week"])):
        week_games = list(week_games)
        r = _records(week_games)
        rows = []
        for g in week_games:
            pa, ph = num(g.get("proj_away")), num(g.get("proj_home"))
            rows.append({
                **scoreboard(g["away"], g["home"], g.get("away_score"), g.get("home_score"),
                             digits=0),
                "proj": None if pa is None or ph is None else f"{pa:.1f} – {ph:.1f}",
                "su": graded_cell(g["su_pick"], g["su_res"]),
                "ats": graded_cell(g["ats_label"], g["ats_res"], g["ats_play"]),
                "ou": graded_cell(g["ou_label"], g["ou_res"], g["ou_play"]),
                "flagged": g["ats_play"] or g["ou_play"],
            })
        line = [f"{r['n']} games", f"SU {_rec(r['su'])}", f"ATS {_rec(r['ats'])}",
                f"O/U {_rec(r['ou'])}"]
        plays = tuple(a + b for a, b in zip(r["spread_plays"], r["total_plays"]))
        if sum(plays):
            line.append(f"★ plays {_rec(plays)}")
        table = {
            "subtitle": f"{season} · Week {wk} — " + " · ".join(line),
            "updated": updated,
            **({"search": search} if search else {}),
            **({"filters": [FLAGGED_FILTER]} if any(row["flagged"] for row in rows) else {}),
            "columns": game_cols,
            "rows": rows,
        }
        slates.append(Slate(week_id(season, wk), f"Week {wk}", table))
        by_week.append({
            "week": {"v": f"Week {wk}", "sort": wk},
            "games": r["n"],
            "su": _rec_cell(r["su"], judged=False),
            "ats": _rec_cell(r["ats"]),
            "ou": _rec_cell(r["ou"]),
            "spreadPlays": _rec_cell(r["spread_plays"]),
            "totalPlays": _rec_cell(r["total_plays"]),
            "mae": r["mae"],
        })

    overview = {
        "subtitle": f"{season} · record by week",
        "updated": updated,
        "columns": [
            {"key": "week", "label": "Week", "sortable": True},
            {"key": "games", "label": "Games", "align": "right"},
            {"key": "su", "label": "Straight up", "align": "right", "sortable": True},
            {"key": "ats", "label": "ATS", "align": "right", "sortable": True},
            {"key": "ou", "label": "O/U", "align": "right", "sortable": True},
            {"key": "spreadPlays", "label": "★ Spread", "align": "right", "sortable": True},
            {"key": "totalPlays", "label": "★ Total", "align": "right", "sortable": True},
            {"key": "mae", "label": "Margin MAE", "align": "right", "format": "number",
             "sortable": True},
        ],
        "rows": by_week,
    }
    # "All weeks" first in the dropdown; the view opens on the latest graded week.
    publish_slates(view_dir, [Slate(f"{season}-all", "All weeks", overview), *slates],
                   season=season, latest=slates[-1].id, summary=summary)


# --------------------------------------------------------------------------
# CLI: validate everything the site will serve
# --------------------------------------------------------------------------
def check_all(data_dir: Path = SITE_DATA) -> int:
    """Validate every table and manifest under data_dir; returns #problems."""
    checked = problems = 0
    for path in sorted(data_dir.rglob("*.json")):
        rel = path.relative_to(data_dir).as_posix()
        try:
            # utf-8-sig: tolerate a BOM from a Windows editor, as browsers do
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if path.name == "index.json":
                check_manifest(payload, rel, path.parent)
            else:
                check_table(payload, rel)
            checked += 1
        except ValueError as exc:  # ContractError, or JSONDecodeError
            problems += 1
            print(f"  FAIL {exc}" if isinstance(exc, ContractError) else f"  FAIL {rel}: {exc}")
    print(f"Checked {checked + problems} data files in {data_dir}: "
          f"{'OK' if not problems else f'{problems} problem(s)'}")
    return problems


if __name__ == "__main__":
    if sys.argv[1:2] != ["check"]:
        raise SystemExit(__doc__)
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else SITE_DATA
    raise SystemExit(1 if check_all(target) else 0)
