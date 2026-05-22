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
// Sports — pro first, college last
// ============================================================
// Each sport gets:
//   id          - URL slug, e.g. /nfl
//   name        - Display name on the card
//   subtitle    - One-line description
//   status      - 'live' | 'in-dev' | 'planned'
//   accent      - Hex color used as accent on the card
//   year        - Season/year tag shown on the card
// ============================================================

export const SPORTS = [
  {
    id: 'nfl',
    name: 'NFL',
    subtitle: 'DVOA + PFF + projection blend',
    status: 'live',
    accent: '#c9a55c',
    year: '2025 SEASON',
  },
  {
    id: 'nba',
    name: 'NBA',
    subtitle: 'Player & team scoring model',
    status: 'in-dev',
    accent: '#e8833a',
    year: '2025–26',
  },
  {
    id: 'mlb',
    name: 'MLB',
    subtitle: 'Game-level run projections',
    status: 'live',
    accent: '#d94545',
    year: '2026 SEASON',
  },
  {
    id: 'nhl',
    name: 'NHL',
    subtitle: 'Lines + GSAX-driven model',
    status: 'in-dev',
    accent: '#6bb6e8',
    year: '2025–26',
  },
  {
    id: 'cfb',
    name: 'COLLEGE FOOTBALL',
    subtitle: 'FBS game & season projections',
    status: 'live',
    accent: '#b34248',
    year: '2025 SEASON',
  },
  {
    id: 'cbb',
    name: 'COLLEGE BASKETBALL',
    subtitle: 'Barttorvik-based game predictor',
    status: 'live',
    accent: '#e89c3a',
    year: '2025–26',
  },
];

// Status badge labels and ordering for the legend
export const STATUS_META = {
  live: { label: 'LIVE', order: 0 },
  'in-dev': { label: 'IN DEVELOPMENT', order: 1 },
  planned: { label: 'PLANNED', order: 2 },
};
