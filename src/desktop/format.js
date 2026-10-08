// Small formatters for the desktop app's run status (times arrive as epoch ms).

// '8:01 PM'
export function clock(ms) {
  return new Date(ms).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

// '0:42', '12:05', '1:02:09'
export function elapsed(fromMs, toMs) {
  const s = Math.max(0, Math.round((toMs - fromMs) / 1000));
  const two = (n) => String(n).padStart(2, '0');
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${two(m)}:${two(s % 60)}` : `${m}:${two(s % 60)}`;
}

// 'just now', '12 min ago', '3 h ago', '2 days ago'
export function ago(ms, nowMs) {
  const s = (nowMs - ms) / 1000;
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 2 * 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

// What a model is doing, in a few words: { tone: 'busy' | 'good' | 'bad' | 'muted', text }.
export function runHeadline(model, nowMs) {
  switch (model.state) {
    case 'planning':
      return { tone: 'busy', text: 'Checking what’s due' };
    case 'running':
    case 'exporting': {
      const step = model.steps.find((s) => s.state === 'running');
      const what = model.state === 'exporting' ? 'Updating site data' : step?.label ?? 'Running';
      return { tone: 'busy', text: `${what} · ${elapsed(model.started, nowMs)}` };
    }
    case 'done':
      return model.steps.some((s) => s.state === 'done')
        ? { tone: 'good', text: `Updated ${clock(model.ended)}` }
        : { tone: 'muted', text: `Up to date · checked ${clock(model.ended)}` };
    case 'failed':
      return { tone: 'bad', text: `Run failed · ${clock(model.ended ?? nowMs)}` };
    default:
      return model.last?.ended
        ? {
            tone: model.last.state === 'failed' ? 'bad' : 'muted',
            text: `Last run ${ago(model.last.ended, nowMs)}${model.last.state === 'failed' ? ' · failed' : ''}`,
          }
        : { tone: 'muted', text: 'Not run yet' };
  }
}
