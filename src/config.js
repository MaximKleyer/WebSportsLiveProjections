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
// Each sport page renders one TAB per view, in this order (`short` is the
// tab label). The label/blurb live here once; each sport only declares WHICH
// views it has and the per-view status — so NFL game projections can be live
// while NFL season is still planned. To ship a model: set a view's status to
// 'live' and drop its data at public/data/<sportId>/<viewType>.json. (See README.)
// ============================================================

export const VIEW_TYPES = {
  games: {
    label: 'Weekly Game Projections',
    short: 'WEEKLY',
    blurb:
      'Score and win-probability projections for every game, week by week.',
    // Weekly views read data/<sport>/<view>/index.json (the week manifest)
    // and one data/<sport>/<view>/<season>-wNN.json file per week.
    weekly: true,
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
};

// Build the standard three views, defaulting to 'planned' unless overridden.
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
    // Players view removed from the site (Sep 2026) — games + season only.
    views: [
      { type: 'games', status: 'live' },
      { type: 'season', status: 'live' },
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
    status: 'live',
    accent: '#d94545',
    year: '2026 SEASON',
    views: views(),
  },
  {
    id: 'nhl',
    name: 'NHL',
    subtitle: 'Lines + GSAX-driven model',
    status: 'in-dev',
    accent: '#6bb6e8',
    year: '2025–26',
    views: views({ games: 'in-dev' }),
  },
  {
    id: 'cfb',
    name: 'COLLEGE FOOTBALL',
    subtitle: 'FBS game & season projections',
    status: 'live',
    accent: '#b34248',
    year: '2026 SEASON',
    // Players view removed from the site (Sep 2026) — games + season only.
    views: [
      { type: 'games', status: 'live' },
      { type: 'season', status: 'live' },
    ],
  },
  {
    id: 'cbb',
    name: 'COLLEGE BASKETBALL',
    subtitle: 'Barttorvik-based game predictor',
    status: 'live',
    accent: '#e89c3a',
    year: '2025–26',
    views: views(),
  },
];

// Status badge labels and ordering for the legend
export const STATUS_META = {
  live: { label: 'LIVE', order: 0 },
  'in-dev': { label: 'IN DEVELOPMENT', order: 1 },
  planned: { label: 'PLANNED', order: 2 },
};
