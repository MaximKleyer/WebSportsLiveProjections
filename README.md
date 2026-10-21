# WebSportsLiveProjections

Weekly sports projections and models across NFL, NBA, MLB, NHL, College Football, and College Basketball. React + Vite, deployed to GitHub Pages.

**Live:** https://maximkleyer.github.io/WebSportsLiveProjections/

## Desktop app

Double-click **Sports Models** on the desktop (`.\desktop\install-shortcut.ps1`
creates the shortcut): a window opens on the latest results while every model
that's due runs in the background, and the landing page's Model desk shows
what ran, what was skipped and why. Each step is protected by a kickoff
freeze — a game that started before the run keeps its earlier projection, so
the graded record stays honest. A scheduled task (`.\desktop\install-schedule.ps1`)
runs the same thing hourly, 9 AM–11 PM, with no window, so predictions exist
before games start even when the app isn't open. Details in
[desktop/README.md](desktop/README.md).

## Updating the data (NFL, CFB, NHL and NBA are wired up)

The models stay pure: each one just writes its CSVs. This repo holds a thin
adapter per model (`scripts/exporters/<sport>.py`) that turns those CSVs into
the site's JSON through a shared toolkit (`scripts/site_export.py`). One
command refreshes everything:

```powershell
.\scripts\update-data.ps1                          # re-export whatever the models last produced
.\scripts\update-data.ps1 -Predict -Week 3         # run every model (football for week 3), then export
.\scripts\update-data.ps1 -Sport nhl -Predict      # NHL: next game day, season sim, power rankings
.\scripts\update-data.ps1 -Grade -Predict -Week 5  # weekly football routine: grade, project week 5, export
```

`-Grade` runs each football model's own grader over every saved week before
anything is predicted (re-grading a finished week reproduces it; a week in
progress counts the games played so far).

Model commands go through the desktop app's protected steps
(`desktop/sports/<sport>.py step …`: the kickoff freeze, a backup, output
checks). It runs each adapter with that model's own venv Python, then validates every
data file (`python scripts/site_export.py check` — CI runs the same check
before deploying). Then review the diff and commit + push this repo — GitHub
Pages redeploys.

The **Results** tab comes from each football model's `results_log.csv`, which
`-Grade` brings up to date (or grade by hand: `python -m nfl_projector_v1 grade
--season 2026 --week N`, `python -m cfb_model.grade --week N`). The NHL
needs no grading step: its adapter scores the published daily predictions
against the final scores (and settled bets) in the model's database, which
each `nhl daily` run brings up to date.

The models live beside this repo in the hub's `models\` folder —
`C:\Users\maxim\WebSportsLiveProjections\models\nfl`, `cfb`, `nhl` and `nba` —
each with its own venv (and, except CFB, its own git repo). The NHL adapter
also reads goalie names from the game-centre pages that model caches under
`data/raw/`. The NBA's day files come from that model's own "NBA gameday"
scheduled task (it predicts on the model's clock through each game day), so
for the NBA the app and `-Predict` only refresh the season sim and power
rankings; its adapter publishes each game's prediction as the model logged it
live (its latest stored on game day before tip-off, by whichever model
version), so a model change keeps the earlier days while a rehearsal's
leftover day files stay off the site.

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
record lives there. A manifest with no slates yet (`"slates": []`,
`"latest": null`) shows as "Nothing published yet" — the NBA before its first
game day.

**Game cards** — click a game's row to open its card. A slate's table names a
`details` file beside it, and each row it opens carries an `"id"`; adapters
pass the cards to `publish_slates` as the `Slate`'s `details`
(`{ <row id>: game }`), which writes `<slate id>.details.json`:

```json
{ "games": { "2026020056": {
  "title": "UTA @ BOS", "subtitle": "Thu Oct 8 · 7:00 PM ET",
  "away": { "name": "Utah Mammoth", "abbr": "UTA", "note": "3–1–0" },
  "home": { "name": "Boston Bruins", "abbr": "BOS" },
  "score": { "away": "3.20", "home": "2.93", "label": "Expected goals" },
  "win": { "away": 0.55, "home": 0.45, "note": "Fair odds: UTA -120, BOS +120" },
  "sections": [
    { "title": "What drives the projection", "kind": "notes", "items": ["…"] },
    { "title": "The model's ratings", "kind": "compare",
      "rows": [{ "label": "Offense", "away": 83, "home": 41, "max": 100 }] },
    { "title": "Probabilities", "kind": "table", "columns": ["", "Model", "Market"],
      "rows": [["UTA to win", "55%", "54%"]] }
  ] } } }
```

- `compare` rows draw paired bars (away left, home right; values ≥ 0, scaled
  to `max` or to the larger of the two); `awayText` / `homeText` replace the
  shown numbers and `"lower": true` marks a row where less is better (goals
  allowed). Table cells are plain values or `{ "v", "tone" }`.
- A card for an earlier day or week keeps to what that prediction recorded:
  team ratings and season odds are today's, so only current slates show them.

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
