import { useState } from 'react';
import { SPORTS } from '../config.js';
import { BUSY_STATES, useDesktop } from './DesktopProvider.jsx';
import LogDialog from './LogDialog.jsx';
import { ago, clock, elapsed, runHeadline } from './format.js';

// The desktop app's overview of its models (landing page, app only): what
// each one ran this time and why, what it skipped and why, and buttons to
// run them or read their logs. Steps and their reasons come from each
// sport's plan (desktop/sports/<id>.py).

const MARKS = { done: '✓', failed: '✕', running: '●', skipped: '–', pending: '○' };

export default function ModelDesk() {
  const desktop = useDesktop();
  const [logFor, setLogFor] = useState(null);
  const [starting, setStarting] = useState(false);
  if (!desktop) return null;

  const { status, error, run } = desktop;
  const models = status?.models ?? [];
  const now = status?.now ?? Date.now();
  const busy = Boolean(status?.busy);
  const running = models.filter((m) => BUSY_STATES.has(m.state)).length;
  const failed = models.filter((m) => m.state === 'failed').length;

  const start = async (options) => {
    setStarting(true);
    try {
      await run(options);
    } finally {
      setStarting(false);
    }
  };

  // Before this session's first pass (opened with --no-run): when the last one was.
  const lastEnded = Math.max(0, ...models.map((m) => m.last?.ended ?? 0));
  let headline;
  if (error) headline = 'The app isn’t answering';
  else if (!status) headline = 'Connecting…';
  else if (running) headline = `${running} running`;
  else if (failed) headline = `${failed} failed`;
  else if (models.every((m) => m.state === 'idle')) {
    headline = lastEnded ? `Last run ${ago(lastEnded, now)}` : 'Not run yet';
  } else headline = 'All up to date';

  const logModel = models.find((m) => m.id === logFor);

  return (
    <section className="model-desk" aria-label="Model runs">
      <div className="grid-section__head">
        <span className="grid-section__label">Model desk</span>
        <span className="grid-section__count">{headline}</span>
      </div>

      <div className="model-desk__bar">
        <div className="desk-actions">
          <button
            type="button"
            className="desk-btn"
            disabled={busy || starting || !status}
            onClick={() => start({ force: false })}
          >
            Run what’s due
          </button>
          <button
            type="button"
            className="desk-btn desk-btn--ghost"
            disabled={busy || starting || !status}
            onClick={() => start({ force: true })}
          >
            Run everything now
          </button>
        </div>
        <CheckNote check={status?.check} />
      </div>

      <ul className="desk-list">
        {models.map((model) => (
          <DeskRow
            key={model.id}
            model={model}
            now={now}
            disabled={starting}
            onRun={() => start({ sports: [model.id], force: true })}
            onLog={() => setLogFor(model.id)}
          />
        ))}
      </ul>

      {logModel && (
        <LogDialog
          sportId={logModel.id}
          name={sportName(logModel)}
          live={BUSY_STATES.has(logModel.state)}
          onClose={() => setLogFor(null)}
        />
      )}
    </section>
  );
}

function sportOf(model) {
  return SPORTS.find((s) => s.id === model.id);
}

function sportName(model) {
  return sportOf(model)?.name ?? model.name;
}

function CheckNote({ check }) {
  if (!check?.state) return null;
  if (check.state === 'running') return <p className="model-desk__note">Checking the site data…</p>;
  if (check.state === 'ok') {
    return <p className="model-desk__note">Site data checked · {clock(check.at)}</p>;
  }
  return (
    <p className="model-desk__note model-desk__note--bad">
      Site data check failed{check.summary ? `: ${check.summary}` : ''}
    </p>
  );
}

function DeskRow({ model, now, disabled, onRun, onLog }) {
  const head = runHeadline(model, now);
  const busy = BUSY_STATES.has(model.state);
  const steps = model.steps ?? [];
  // Before this session's first pass, show how the last one went.
  const earlier = model.state === 'idle' ? model.last?.summaries ?? [] : [];

  return (
    <li className="desk-row" style={{ '--accent': sportOf(model)?.accent }}>
      <div className="desk-row__main">
        <div className="desk-row__title">
          <span className="desk-row__name">{sportName(model)}</span>
          {(model.label || model.last?.label) && (
            <span className="desk-row__label">{model.label || model.last.label}</span>
          )}
        </div>
        <div className="desk-row__meta">
          <p className={`run-line run-line--${head.tone}`}>
            <span className="run-line__dot" aria-hidden="true" />
            {head.text}
          </p>
          {model.info?.map((stat) => (
            <span key={stat.label} className={`desk-stat${stat.tone ? ` desk-stat--${stat.tone}` : ''}`}>
              <span className="desk-stat__label">{stat.label}</span>
              {stat.value}
              {stat.detail && <span className="desk-stat__detail">{stat.detail}</span>}
            </span>
          ))}
        </div>
      </div>

      <div className="desk-row__buttons">
        <button type="button" className="desk-btn desk-btn--ghost" disabled={busy || disabled} onClick={onRun}>
          Run now
        </button>
        <button type="button" className="desk-btn desk-btn--ghost" onClick={onLog}>
          Log
        </button>
      </div>

      {steps.length > 0 && (
        <ol className="desk-steps">
          {steps.map((step) => (
            <li key={step.id} className={`desk-step desk-step--${step.state}`}>
              <span className="desk-step__mark" aria-hidden="true">
                {MARKS[step.state] ?? '○'}
              </span>
              <span className="desk-step__label">{step.label}</span>
              <span className="desk-step__detail">{stepDetail(step, now, model.forced)}</span>
              {step.warnings?.map((w) => (
                <span key={w} className="desk-step__warn">
                  ⚠ {w}
                </span>
              ))}
            </li>
          ))}
        </ol>
      )}

      {earlier.length > 0 && (
        <ul className="desk-earlier">
          {earlier.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {model.notes?.map((note) => (
        <p key={note} className="desk-row__note">
          {note}
        </p>
      ))}
      {model.error && <p className="desk-row__error">{model.error}</p>}
    </li>
  );
}

function stepDetail(step, now, forced) {
  switch (step.state) {
    case 'running':
      return `running · ${elapsed(step.started, now)}`;
    case 'done':
      return `${step.summary} · ${elapsed(step.started, step.ended)}`;
    case 'failed':
      return step.summary;
    case 'skipped':
      return step.summary || step.reason;
    default:
      return forced ? 'queued (run now)' : `queued · ${step.reason}`;
  }
}
