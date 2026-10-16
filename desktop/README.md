# Sports Models — desktop app

Double-click **Sports Models** on the desktop: a window opens on the latest
results, and every model that's due runs in the background, each in its own
folder with its own venv. The landing page's **Model desk** shows what each
one ran, what it skipped and why, with buttons to run one now or read its log.
Pages refresh as each model's data is re-exported. Closing the window closes
the app (once any model mid-run has finished, so nothing is half-written).

```powershell
.\desktop\install-shortcut.ps1        # (re)create the desktop shortcut
.\desktop\install-schedule.ps1        # the hourly background run (-Remove to delete it)
python desktop\app.py --no-window     # server only: http://127.0.0.1:5190/
python desktop\app.py --no-run        # open without running anything
```

**Hourly background run.** The app only runs while it's open, so a Windows
scheduled task ("Sports Models", 9 AM–11 PM, every hour) runs
`app.py --background`: the app's *Run what's due*, with no window, then it
exits — so predictions exist before games start whether or not you open the
app. If the app is open, the run happens there instead (you see it); if you
open the app mid-run, the window joins it. A run missed while the PC was off
or asleep happens as soon as it's back.

**Your inputs.** Each model's row lists what only you can give it (its plan's
`inputs`, from `Plan.need` in `desktop/sports/<id>.py`), and a line above the
desk names whatever is due. Where the model's files can tell, an item checks
itself: NFL's weekly FantasyPoints exports (all five files in
`data\raw\<season>\week_NN\` for every finished week), the injury report (from
the Friday before the week's Sunday games) and the warehouse rebuild (due while
any export is newer than `warehouse.duckdb`); CFB's retrain (every 3 weeks,
`models\current\metadata.json`) and last week's injury adjustments
(`config.yaml` `overrides`, stale from Monday); NBA's backtest (its model state
against `nba version`). The rest are reminders. A list refreshes with each run,
and on its own: **Check** re-reads that model's files at once (its plan alone,
`POST /api/check`; no model work), and an input found in place drops off the
list. A file there under a near-miss name (`advanced_recieving_player.csv`) gets
a ⚠ line naming it and the name it needs — the model reads files by their exact
names and skips the rest without a word. NFL's exports are read too, every
finished week each time: one that covers more than a week (`G` above 1, a
season-to-date export the build would count twice) or is the wrong view (a
player view under a defense name — player views have `POS` — or a rushing view
as receiving: `CMP`, `YPC`, `TGT`) keeps its week on the list with a ⚠ line until
it's replaced, and puts a ⚠ on the rebuild meanwhile. So does a week whose player
exports have no rows for a game the schedule says was played (one saved Tuesday
morning can miss Monday night's), its ⚠ line naming the game; that one doesn't
hold the rebuild. **Done** clears a
reminder the app can't verify until it's due again (the plan's `again`: next
Tuesday for QB changes, next Monday for CFB's adjustments, next morning for the
daily ones), kept in `.state/state.json` `dismissed`.

**Staying awake mid-run.** While any model runs, the app asks Windows not to
idle into sleep or Modern Standby (`SetThreadExecutionState`, system required —
the screen can still turn off; the lid and power button still sleep the PC), and
lets go when the last one finishes: asleep, Windows freezes the models mid-run
(on 2026-10-10 a run started at 1:06 PM sat frozen until 8:09 the next morning).

No install: the app is the standard library on the system Python (3.11+), the
window is Microsoft Edge in app mode with its own profile
(`%LOCALAPPDATA%\SportsModels`), and it serves the built site from `dist/`
(rebuilt automatically when `src/` changes) with data straight from
`public/data/`. Publishing is still yours: review the diff, commit, push.

## What runs when

Each sport's rules live in `desktop/sports/<id>.py` (`plan`), so a model only
runs when its inputs can have changed and its API budget allows:

| Model | Step | Runs when |
|---|---|---|
| NFL | grade | a projected game finished and isn't graded yet (retries every 2 h) |
| | QB rushing | the props layer's QB carries and rushing yards from nflverse (free, 2-8 MB): once a week's last game is 6 h old, then every 6 h for up to 2 days while a team of that week is missing; regular season |
| | predict | the current week has games left and was last projected 12 h+ ago |
| | season sim | after new projections, or weekly |
| | prop lines | hourly from 4 h before a kickoff to 1 h after it, every 4 h otherwise (ESPN, free; regular season) |
| | freeze props | the props paper log: once early (the first run within 36 h of Monday night's game, before most lines open), after each warehouse rebuild, and once from Saturday 6 AM ET (final injury report); regular season only |
| | grade paper log | a frozen week has finished and the warehouse is newer than the last grading |
| CFB | grade | a projected game finished and isn't graded (2 API calls; retries every 3 h) |
| | project | the current week has games left, last projected 36 h+ ago, 60+ API calls left |
| | season sim | after new projections, or weekly |
| NHL | daily | 30 min+ since the last run, the app's or not (3 h if `ODDS_API_KEY` is set: 3 credits a run) |
| | season sim, power | after new predictions, or every 20 h |

**Run now** (or **Run everything now**) ignores the timing — not the freeze.
The current week comes from each model's cached schedule (NFL: the nflverse
feed; CFB: the CollegeFootballData `/games` cache), so working it out costs no
API calls.

## The kickoff freeze

A model's slate file (a week's or a day's games) is what its grading scores,
so a projection there must have been made **before** that game started. Every
`step` backs the file up (`desktop/.state/backups/`, kept 45 days), runs the
model, then puts back the earlier rows of every game that had kicked off when
the run began — or drops a projection made after kickoff when there is no
earlier one. Steps also fail when a model writes nothing (and the previous file
is put back), and warn when games are missing or CFB got no betting lines.

The NFL model has its own, coarser guard: once any game of the week has a final
score in its warehouse (after a rebuild from your FantasyPoints exports), it
leaves the week's file alone and writes the run to `predictions_*_rerun.csv` —
games still to come included, which the site never reads. The step merges that
file back game by game: games that have kicked off keep their rows, the rest
take the run's.

`scripts/update-data.ps1` runs its model commands through the same `step`, so
manual runs are protected too. A step that dies mid-run (killed, a crash) leaves
a note in `desktop/.state/pending/`; the sport's next plan or step finishes the
freeze from the backup once the model process has exited.

**Games no run predicted in time (NHL).** A game that started before any run
predicted it can't be graded. `nhl daily` (and `nhl.py step rebuild --date D`)
rebuilds its numbers as of a minute before puck drop — the model's own
pipeline, offline, on a throwaway copy of its database, so its predictions,
bets and record are untouched — into `outputs/daily/<date>_rebuilt.csv`. The
Daily tab shows those games greyed out ("rebuilt · not graded"); grading reads
only `<date>_predictions.csv`.

## Adding a model

1. An entry in `desktop/models.toml` (folder and venv).
2. `desktop/sports/<id>.py`: `plan` (what's due and why) and its `step`s —
   `nhl.py` is the short one to copy.
3. The usual site pieces: `scripts/exporters/<id>.py` and its views in
   `src/config.js` (see the main README).

## Files

- `app.py` — single instance, window, lifecycle; `server.py` — site + API;
  `runner.py` — passes, logs, history. Standard library only.
- `sports/` — per-model rules and protected steps (run in each model's venv).
- `.state/` (git-ignored) — `state.json` (when each step last ran), `logs/`,
  `backups/`, `app.log`.
