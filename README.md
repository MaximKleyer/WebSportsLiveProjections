# Sports Models

Landing page + per-sport routes for game, player, and season projections across NFL, CFB, CBB, MLB, NHL, and NBA. React + Vite, deploys to GitHub Pages.

## One-time setup

```bash
npm install
npm run dev
```

## Before deploying — edit `src/config.js`

Two values must be changed before the first deploy:

```js
export const GITHUB_USERNAME = 'YOUR_USERNAME_HERE'; // your GitHub handle
export const REPO_NAME = 'sports-models';            // the repo this lives in
```

`REPO_NAME` must match the repo on GitHub exactly — it's used as the Vite `base` path so assets resolve correctly under `<username>.github.io/<repo-name>/`. Forgetting this gives a blank page on deploy.

The rest of `config.js` controls:
- `BRAND` — wordmark, tagline, footer byline
- `SPORTS` — order, name, subtitle, status (`live` / `in-dev` / `planned`), accent color, year tag

## Deploy

```bash
git init && git add -A && git commit -m "initial"
git remote add origin git@github.com:USERNAME/REPO.git
git push -u origin main
```

Then in the repo settings: **Pages → Source → GitHub Actions**. The workflow in `.github/workflows/deploy.yml` builds and deploys on push to `main`.

## Project structure

```
src/
├── config.js              # username, repo, brand, sport list
├── components/
│   ├── Header.jsx         # wordmark + sport nav + github link
│   ├── Footer.jsx
│   └── SportCard.jsx      # grid card used on landing
├── pages/
│   ├── Landing.jsx        # hero + sport grid
│   ├── SportPage.jsx      # generic placeholder, one per sport route
│   └── NotFound.jsx
├── styles/
│   └── global.css         # all styling — design tokens at the top
├── App.jsx                # routing
└── main.jsx
```

Routing uses `HashRouter` so deep links work on GitHub Pages without a 404 redirect hack.

## Adding a real sport model

Each sport currently renders the same `<SportPage />` placeholder. To replace one with real content, e.g. for NFL:

1. Create `src/pages/NFLPage.jsx` with the actual projections UI.
2. In `App.jsx`, swap the `/nfl` route's element from `<SportPage sport={...} />` to `<NFLPage />`.

The remaining sports keep using the generic placeholder until you build them.

## Adding a new sport

Append an entry to `SPORTS` in `src/config.js`. The card, nav link, and route all wire up automatically.
