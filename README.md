# WebSportsLiveProjections

Weekly sports projections and models across NFL, NBA, MLB, NHL, College Football, and College Basketball. React + Vite, deployed to GitHub Pages.

**Live:** https://maximkleyer.github.io/WebSportsLiveProjections/

## Updating the data (NFL, CFB and NHL are wired up)

The models stay pure: each one just writes its CSVs. This repo holds a thin
adapter per model (`scripts/exporters/<sport>.py`) that turns those CSVs into
the site's JSON through a shared toolkit (`scripts/site_export.py`). One
command refreshes everything:

```powershell
.\scripts\update-data.ps1                          # re-export whatever the models last produced
.\scripts\update-data.ps1 -Predict -Week 3         # run every model (football for week 3), then export
.\scripts\update-data.ps1 -Sport nhl -Predict      # NHL: next game day, season sim, power rankings
```

It runs each adapter with that model's own venv Python, then validates every
data file (`python scripts/site_export.py check` — CI runs the same check
before deploying). Then review the diff and commit + push this repo — GitHub
Pages redeploys.

The **Results** tab comes from each football model's `results_log.csv`, so
after grading (`python -m nfl_projector_v1 grade --season 2026 --week N`,
`python -m cfb_model.grade --week N`) the same script refreshes it. The NHL
needs no grading step: its adapter scores the published daily predictions
against the final scores (and settled bets) in the model's database, which
each `nhl daily` run brings up to date.

Model repos: `C:\Users\maxim\NFLProjectionModel\nfl_projector_v1`,
`C:\Users\maxim\CFB_Projection_Model` and `C:\Users\maxim\NHL_Projection_Model`
(the NHL adapter also reads goalie names from the game-centre pages that
model caches under `data/raw/`).

## Adding a model

1. Write `scripts/exporters/<sport>.py`: read the model's CSVs (pandas is
   fine — it runs in the model's venv) and build tables with
   `site_export`'s helpers (`write_table`, `publish_slates`,
   `publish_results`, …). Every write is validated against the contract below.
2. Add the sport to `scripts/update-data.ps1`.
3. In `src/config.js`, list the sport's views and flip their status to
   `'live'`. A sport can relabel a view, e.g. a daily MLB slate:
   `{ type: 'games', status: 'live', short: 'DAILY', label: 'Daily Game Projections' }`.

The page then fetches, renders via the shared `ProjectionTable`, and handles
loading / empty / error states. No per-sport rendering code.

## Data contract

Model output is plain static JSON under `public/data/<sport>/` — no backend.
`scripts/site_export.py` documents and enforces it.

**Tables** — single-file views (`season.json`, `rankings.json`) and every slate:

```json
{
  "subtitle": "2026 · Week 4",
  "updated": "2026-09-25",
  "search": "Search teams",
  "filters": [{ "field": "flagged", "label": "★ plays only" }],
  "columns": [
    { "key": "home", "label": "Home" },
    { "key": "homeWin", "label": "Home Win", "align": "right", "format": "percent", "sortable": true },
    { "key": "ats", "label": "ATS (edge)", "align": "right", "sortable": true }
  ],
  "rows": [
    { "home": "Rutgers", "homeWin": 0.62, "flagged": true,
      "ats": { "v": "★ Rutgers +3.0", "sort": 3.0 } }
  ]
}
```

- `format`: `percent` (expects 0–1) or `number`; `align`: `left` (default),
  `right`, `center`; `sortable: true` makes the header clickable.
- A cell is a plain value or `{ "v", "tone"?, "sort"?, "sub"? }` — `tone` is
  `good` / `bad` / `muted` (hit / miss / push); `sort` is what the column
  orders by when the shown text isn't (a kickoff time, an edge size); `sub` is
  a second, smaller line under the value (the NHL's likely starting goalie).
- `search` (placeholder text) adds a search box; `filters` add toggles that
  keep rows whose `row[field]` is truthy. Rows may carry such extra fields.
- `"groupBy": "<row field>"` (e.g. `"division"`) renders one sub-table per
  distinct value; sorting then applies within each group.
- `"summary": { "title", "stats": [{ "label", "value", "detail", "tone" }], "note" }`
  renders headline tiles above the table.

**Slate views** (games, results — `slates: true` in `VIEW_TYPES`): one table
per *slate* — a football week, an MLB day — plus a manifest the dropdown reads:

```json
// public/data/<sport>/games/index.json
{
  "season": 2026,
  "label": "2026 Season",
  "unit": "week",
  "latest": "2026-w04.json",
  "slates": [
    { "id": "2026-w03", "label": "Week 3", "file": "2026-w03.json" },
    { "id": "2026-w04", "label": "Week 4", "file": "2026-w04.json" }
  ]
}
```

A manifest may carry a `summary` too — the Results tab's season-to-date
record lives there.

**Landing card** — each live sport's card shows a live line from
`public/data/<sport>/card.json` (written by its adapter via `write_card`):

```json
{ "headline": "Week 4", "updated": "2026-09-25",
  "stats": [{ "label": "Season ATS", "value": "130–126–3", "detail": "50.8%", "tone": "bad" }] }
```

## Links

Every view has a shareable URL: the open tab and slate live in the address,
e.g. `#/cfb?tab=results&week=2026-w03` or `#/nhl?day=2026-10-01` (`tab` is
the view type — `games`, `season`, `results`, `rankings`; the slate parameter
is the manifest's `unit`). A missing or unknown value falls back to the
default tab / latest slate.
