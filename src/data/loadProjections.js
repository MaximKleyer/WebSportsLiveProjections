// Fetch model output for the site's views.
//
// Model output lives as static JSON under public/data/<sportId>/…, so shipping
// a new model is "drop files + flip a status flag in config.js" — no backend.
//
// Two layouts:
//   single-file views:  data/<sportId>/<viewType>.json
//   weekly views:       data/<sportId>/<viewType>/index.json   (week manifest)
//                       data/<sportId>/<viewType>/<season>-wNN.json (one per week)
//
// Table shape (both layouts):
//   { subtitle?, updated?, columns: [{ key, label, align?, format? }], rows: [ {…} ] }
// Manifest shape:
//   { season, label?, latest: "<file>", weeks: [{ week, label, file }] }

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

export function loadWeeklyIndex(sportId, viewType) {
  return loadJson(`data/${sportId}/${viewType}/index.json`);
}

export function loadWeeklyWeek(sportId, viewType, file) {
  return loadJson(`data/${sportId}/${viewType}/${file}`);
}
