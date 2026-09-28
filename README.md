# WebSportsLiveProjections

Weekly sports projections and models across NFL, NBA, MLB, NHL, College Football, and College Basketball. React + Vite, deployed to GitHub Pages.

**Live:** https://maximkleyer.github.io/WebSportsLiveProjections/

## Updating the data (NFL + CFB are wired up)

Each model repo has a `scripts/export_web.py` that converts its CSVs into this
site's JSON. One command refreshes everything:

```powershell
.\scripts\update-data.ps1                   # re-export whatever the models last produced
.\scripts\update-data.ps1 -Predict -Week 3  # run both models for week 3, then export
```

Then review the diff and commit + push this repo — GitHub Pages redeploys.

The **Results** tab comes from each model's `results_log.csv`, so after
grading (`python -m nfl_projector_v1 grade --season 2026 --week N`,
`python -m cfb_model.grade --week N`) the same script refreshes it.

Model repos: `C:\Users\maxim\NFLProjectionModel\nfl_projector_v1` and
`C:\Users\maxim\CFB_Projection_Model`.

## Adding a model

Model output is plain static JSON — no backend. Each sport page renders one
tab per *view* (`games`, `players`, `season`, `results`), defined in
`src/config.js`. Optional extras any table can use:

- `"groupBy": "<row field>"` (e.g. `"division"`) renders one sub-table per
  distinct value of that field.
- A cell may be `{ "v": "★ ATL +6 ✓", "tone": "good" }` instead of a plain
  value — `tone` is `good` / `bad` / `muted` (hit / miss / push).
- `"summary": { "title", "stats": [{ "label", "value", "detail", "tone" }], "note" }`
  renders headline tiles above the table (a weekly manifest may carry one
  too — the Results tab's season-to-date record lives there).

**Single-file views** (players, season) live at
`public/data/<sport>/<view>.json`:

```json
{
  "subtitle": "Week 1 — model blend",
  "updated": "2026-09-04",
  "columns": [
    { "key": "game", "label": "Game" },
    { "key": "homeWinProb", "label": "Home Win", "align": "right", "format": "percent" }
  ],
  "rows": [{ "game": "DAL @ PHI", "homeWinProb": 0.62 }]
}
```

`format` supports `percent` (expects 0–1) and `number`; `align` is `left`
(default), `right`, or `center`.

**Weekly views** (games, results — any view with `weekly: true` in `VIEW_TYPES`) live
under `public/data/<sport>/<view>/`: one table file per week plus a manifest
the week picker reads:

```json
// public/data/<sport>/games/index.json
{
  "season": 2026,
  "label": "2026 Season",
  "latest": "2026-w02.json",
  "weeks": [
    { "week": 1, "label": "Week 1", "file": "2026-w01.json" },
    { "week": 2, "label": "Week 2", "file": "2026-w02.json" }
  ]
}
```

Each week file (`2026-w01.json`, …) uses the same table shape as above.

**Flip the status** for the view in `src/config.js`
(e.g. `views: views({ games: 'live' })`) and the page fetches, renders via the
shared `ProjectionTable`, and handles loading / empty / error states — weekly
views get the week picker automatically. View labels/blurbs live in
`VIEW_TYPES` (`src/config.js`).
