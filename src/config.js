// ============================================================
// EDIT THESE TWO VALUES BEFORE DEPLOYING
// ============================================================

// Your GitHub username (used to build the github.io link in the nav)
export const GITHUB_USERNAME = 'maximkleyer';

// The name of the GitHub repo this project lives in.
// Must match exactly — it's used as the Vite `base` path so that
// assets resolve correctly under <username>.github.io/<repo-name>/.
export const REPO_NAME = 'WebSportsLiveProjections';

// ============================================================
// Branding
// ============================================================

export const BRAND = {
  // Shown in the top-left of the header
  wordmark: 'MAXIM',
  // Small subtitle below the wordmark on the landing hero
  tagline: 'Weekly sports projections & models',
  // Shown in the footer
  byline: 'Maxim Kleyer',
};

// ============================================================
// Views — the kinds of model output a sport page can show
// ============================================================
// Each sport page renders one TAB per view, in the order the sport lists them
// (`short` is the tab label). The label/blurb live here once; each sport only
// declares WHICH views it has and the per-view status — so NFL game
// projections can be live while NFL season is still planned. To ship a model:
// set a view's status to 'live' and export its data. (See README.)
// ============================================================

export const VIEW_TYPES = {
  games: {
    label: 'Weekly Game Projections',
    short: 'WEEKLY',
    blurb:
      'Score and win-probability projections for every game, week by week.',
    // A slate view: data/<sport>/<view>/index.json lists its slates (a
    // football week, an MLB day) with a dropdown; each slate is one table.
    slates: true,
  },
  players: {
    label: 'Player Projections',
    short: 'PLAYERS',
    blurb:
      'Per-player stat lines with confidence intervals and matchup adjustments.',
  },
  season: {
    label: 'Full-Season Projections',
    short: 'FULL SEASON',
    blurb:
      'Team standings, playoff odds, and individual award tracking through the season.',
  },
  results: {
    label: 'Results & Track Record',
    short: 'RESULTS',
    blurb:
      'Every pick graded against the final score and the betting line — season to date and week by week.',
    // A slate view like games (one slate per graded week, plus "All weeks");
    // the manifest's `summary` holds the season-to-date record tiles.
    slates: true,
  },
  rankings: {
    label: 'Power Rankings',
    short: 'RANKINGS',
    blurb:
      'Every team ranked by the model’s current ratings, with the components behind them.',
  },
};

// A view's display text: the VIEW_TYPES defaults, which a sport may override
// per view — e.g. a daily MLB slate:
//   { type: 'games', status: 'live', short: 'DAILY', label: 'Daily Game Projections' }
export function viewMeta(view) {
  const base = VIEW_TYPES[view.type] ?? { label: view.type, blurb: '' };
  const overrides = Object.fromEntries(
    ['label', 'short', 'blurb'].filter((k) => view[k]).map((k) => [k, view[k]])
  );
  return { ...base, ...overrides };
}

// One entry per view type, defaulting to 'planned' unless overridden.
const views = (overrides = {}) =>
  Object.keys(VIEW_TYPES).map((type) => ({
    type,
    status: overrides[type] ?? 'planned',
  }));

// ============================================================
// Sports — pro first, college last
// ============================================================
// Each sport gets:
//   id          - URL slug, e.g. /nfl
//   name        - Display name on the card
//   subtitle    - One-line description
//   status      - 'live' | 'in-dev' | 'planned'  (headline badge on the card)
//   accent      - Hex color used as accent on the card
//   year        - Season/year tag shown on the card
//   views       - Per-view model availability (see VIEW_TYPES above)
// ============================================================

export const SPORTS = [
  {
    id: 'nfl',
    name: 'NFL',
    subtitle: 'DVOA + PFF + projection blend',
    status: 'live',
    accent: '#c9a55c',
    year: '2026 SEASON',
    // Players view removed from the site (Sep 2026).
    views: [
      { type: 'games', status: 'live' },
      { type: 'season', status: 'live' },
      { type: 'results', status: 'live' },
    ],
  },
  {
    id: 'nba',
    name: 'NBA',
    subtitle: 'Player & team scoring model',
    status: 'in-dev',
    accent: '#e8833a',
    year: '2025–26',
    views: views({ games: 'in-dev' }),
  },
  {
    id: 'mlb',
    name: 'MLB',
    subtitle: 'Game-level run projections',
    status: 'in-dev',
    accent: '#d94545',
    year: '2026 SEASON',
    views: views({ games: 'in-dev' }),
  },
  {
    id: 'nhl',
    name: 'NHL',
    subtitle: 'Lines + GSAX-driven model',
    status: 'live',
    accent: '#6bb6e8',
    year: '2026–27',
    // Hockey plays every day, so its games view is a daily slate.
    views: [
      {
        type: 'games',
        status: 'live',
        short: 'DAILY',
        label: 'Daily Game Projections',
        blurb:
          'Expected score, win probability, puck line and totals for every game, day by day — with each side’s likely starting goalie.',
      },
      { type: 'season', status: 'live' },
      {
        type: 'results',
        status: 'live',
        blurb:
          'Every published prediction graded against the final score — win picks, probability accuracy and totals, day by day.',
      },
      { type: 'rankings', status: 'live' },
    ],
  },
  {
    id: 'cfb',
    name: 'COLLEGE FOOTBALL',
    subtitle: 'FBS game & season projections',
    status: 'live',
    accent: '#b34248',
    year: '2026 SEASON',
    // Players view removed from the site (Sep 2026).
    views: [
      { type: 'games', status: 'live' },
      { type: 'season', status: 'live' },
      { type: 'results', status: 'live' },
    ],
  },
  {
    id: 'cbb',
    name: 'COLLEGE BASKETBALL',
    subtitle: 'Barttorvik-based game predictor',
    status: 'in-dev',
    accent: '#e89c3a',
    year: '2025–26',
    views: views({ games: 'in-dev' }),
  },
];

// Status badge labels and ordering for the legend
export const STATUS_META = {
  live: { label: 'LIVE', order: 0 },
  'in-dev': { label: 'IN DEVELOPMENT', order: 1 },
  planned: { label: 'PLANNED', order: 2 },
};
