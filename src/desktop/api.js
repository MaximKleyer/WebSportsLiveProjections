// Talking to the desktop app (desktop/server.py). The app marks the page it
// serves with window.__SPORTS_DESKTOP__; the public site never has it, so
// none of this runs on GitHub Pages.

export const IS_DESKTOP = typeof window !== 'undefined' && window.__SPORTS_DESKTOP__ === true;

// The app refuses POSTs without this header (no page on another site can send it).
const HEADERS = { 'Content-Type': 'application/json', 'X-Sports-Models': '1' };

export async function getStatus() {
  const res = await fetch('/api/status', { cache: 'no-store' });
  if (!res.ok) throw new Error(`The app answered HTTP ${res.status}`);
  return res.json();
}

// Start a pass: every model or `sports` (ids); `force` runs every step, however recently it ran.
export async function startRun({ sports, force = false } = {}) {
  const res = await fetch('/api/run', {
    method: 'POST',
    headers: HEADERS,
    body: JSON.stringify({ sports, force }),
  });
  if (!res.ok) throw new Error(`The app answered HTTP ${res.status}`);
  return res.json();
}

// Re-check one model's inputs against its files now (runs only its plan): its fresh checklist.
export async function checkInputs(sport) {
  const res = await fetch('/api/check', { method: 'POST', headers: HEADERS, body: JSON.stringify({ sport }) });
  if (!res.ok) throw new Error((await res.text()) || `The app answered HTTP ${res.status}`);
  return (await res.json()).inputs;
}

// Mark a reminder Done: hidden until it's due back.
export async function dismissInput(sport, id) {
  const res = await fetch('/api/dismiss', { method: 'POST', headers: HEADERS, body: JSON.stringify({ sport, id }) });
  if (!res.ok) throw new Error((await res.text()) || `The app answered HTTP ${res.status}`);
}

export async function getLog(sportId) {
  const res = await fetch(`/api/log?sport=${encodeURIComponent(sportId)}`, { cache: 'no-store' });
  return res.ok ? res.text() : '';
}

// The app closes itself once no window is open: each window pings while
// open and says goodbye as it closes (a beacon still goes out on close).
const WINDOW_ID = IS_DESKTOP ? crypto.randomUUID() : null;

export function ping() {
  return fetch('/api/ping', { method: 'POST', headers: HEADERS, body: JSON.stringify({ id: WINDOW_ID }) }).catch(
    () => {}
  );
}

export function bye() {
  navigator.sendBeacon('/api/bye', JSON.stringify({ id: WINDOW_ID }));
}
