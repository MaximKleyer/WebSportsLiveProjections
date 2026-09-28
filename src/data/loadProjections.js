// Fetch model output for the site's views.
//
// Model output lives as static JSON under public/data/<sportId>/…, so shipping
// a new model is "drop files + flip a status flag in config.js" — no backend.
//
// Two layouts:
//   single-file views:  data/<sportId>/<viewType>.json
//   slate views:        data/<sportId>/<viewType>/index.json   (the manifest)
//                       data/<sportId>/<viewType>/<slate id>.json (one table per
//                       slate — a football week '2026-w03', an MLB day)
//
// The full contract (table, cell, manifest, summary shapes) is documented and
// validated in scripts/site_export.py; every exporter writes through it.

async function loadJson(relPath) {
  // BASE_URL keeps paths correct under the GitHub Pages subpath.
  const res = await fetch(`${import.meta.env.BASE_URL}${relPath}`);
  if (!res.ok) {
    throw new Error(`No data at ${relPath} (HTTP ${res.status})`);
  }
  return res.json();
}

export function loadProjections(sportId, viewType) {
  return loadJson(`data/${sportId}/${viewType}.json`);
}

export function loadSlateIndex(sportId, viewType) {
  return loadJson(`data/${sportId}/${viewType}/index.json`);
}

export function loadSlate(sportId, viewType, file) {
  return loadJson(`data/${sportId}/${viewType}/${file}`);
}
