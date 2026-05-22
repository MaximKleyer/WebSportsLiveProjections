# WebSportsLiveProjections

Weekly sports projections and models across NFL, NBA, MLB, NHL, College Football, and College Basketball. React + Vite, deployed to GitHub Pages.

**Live:** https://maximkleyer.github.io/WebSportsLiveProjections/

---

## First-time setup

### 1. Create the GitHub repo

Go to **github.com/new**:

- **Repository name:** `WebSportsLiveProjections` (must match `REPO_NAME` in `src/config.js`)
- **Visibility:** Public (required for free GitHub Pages)
- **Do NOT** check "Add a README", ".gitignore", or "license" — this project already includes them, and pre-creating them causes a merge conflict on first push.

### 2. Run it locally

```bash
cd WebSportsLiveProjections
npm install
npm run dev
```

Opens at **http://localhost:5173/WebSportsLiveProjections/**. The `/WebSportsLiveProjections/` path matters — Vite uses the `base` from `src/config.js` so the local dev URL matches the production URL. Edits to `.jsx` or `.css` hot-reload instantly.

### 3. Verify the production build

```bash
npm run build      # builds the static site into /dist
npm run preview    # serves /dist locally for sanity check
```

Preview runs at **http://localhost:4173/WebSportsLiveProjections/**. If it renders here, GitHub Pages will render too. Blank page on either is almost always a `REPO_NAME` mismatch in `src/config.js`.

### 4. Push to GitHub

```bash
git init
git add -A
git commit -m "initial scaffold"
git branch -M main
git remote add origin https://github.com/maximkleyer/WebSportsLiveProjections.git
git push -u origin main
```

### 5. Enable GitHub Pages

In the repo on GitHub: **Settings → Pages → Source** → select **GitHub Actions** (not "Deploy from a branch").

Then check the **Actions** tab — the "Deploy" workflow from your push should be running. First run is ~1 minute. Every subsequent push to `main` auto-redeploys.

---

## Day-to-day

```bash
npm run dev        # local development with hot reload
npm run build      # production build into /dist
npm run preview    # serve /dist locally before pushing
git push           # triggers auto-deploy
```

---

## Editing the site

Everything visitor-facing lives in `src/config.js`:

- `GITHUB_USERNAME` and `REPO_NAME` — only touch these if renaming the repo or moving to a different account
- `BRAND.wordmark` / `BRAND.tagline` / `BRAND.byline` — header brand, hero subtitle, footer credit
- `SPORTS` array — order, name, subtitle, status, accent color, year tag. Changing this updates the nav, the landing grid, and the routes simultaneously.

### Status flags

Each sport has a `status` of `'live'`, `'in-dev'`, or `'planned'`. This drives the badge on the card and the dot color. Flip a sport from `in-dev` to `live` when its real page ships.

### Adding a new sport

Append an entry to `SPORTS` in `src/config.js`. The nav link, landing card, and `/sport-id` route all wire up automatically off that one entry.

### Wiring up a real sport model (later)

Each sport currently renders the same generic `<SportPage />` placeholder. To replace one with real content, e.g. NFL:

1. Create `src/pages/NFLPage.jsx` with the actual projections UI.
2. In `src/App.jsx`, change the `/nfl` route's element from `<SportPage sport={...} />` to `<NFLPage />`.

The remaining sports keep using the generic placeholder until you build them.

---

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

Routing uses `HashRouter` so deep links work on GitHub Pages without a custom 404 redirect.

---

## Common pitfalls

- **Blank page after deploy** → `REPO_NAME` in `src/config.js` doesn't match the actual GitHub repo name. The Vite `base` won't resolve.
- **Push rejected with "fetch first"** → You let GitHub auto-create a README during repo setup. Either reset the remote (`git pull origin main --allow-unrelated-histories`) or delete the repo and recreate it without the README.
- **404 on a sport route after refresh** → Shouldn't happen with `HashRouter`, but if it does, confirm `main.jsx` is using `HashRouter` not `BrowserRouter`.
- **Deploy workflow never runs** → Settings → Pages → Source isn't set to "GitHub Actions". The workflow file exists but Pages needs to be explicitly enabled.
