import { useEffect, useRef } from 'react';

// One game's card, opened from its row in a slate table. Generic, like the
// tables: every sport's exporter writes the same shape (scripts/site_export.py,
// "details") and this renders it — a header with the two teams, the projected
// score and the win probability, then sections of three kinds:
//   notes    a few sentences on what drives the projection
//   compare  paired bars, away to the left of the label and home to the right
//   table    a small table (markets, bets, players)
// Away is always blue and home orange (a colorblind-safe pair on this
// surface); every value is printed, so color never carries meaning alone.

const pct = (p) => `${Math.round(p * 100)}%`;

function short(team) {
  return team.abbr || team.name;
}

function cellParts(cell) {
  return cell !== null && typeof cell === 'object'
    ? { value: cell.v, tone: cell.tone }
    : { value: cell, tone: null };
}

function WinBar({ win, away, home }) {
  return (
    <div className="game-card__win">
      <div className="win-bar__labels">
        <span>
          {short(away)} <strong>{pct(win.away)}</strong>
        </span>
        <span className="win-bar__title">Win probability</span>
        <span>
          <strong>{pct(win.home)}</strong> {short(home)}
        </span>
      </div>
      <div
        className="win-bar"
        role="img"
        aria-label={`Win probability: ${away.name} ${pct(win.away)}, ${home.name} ${pct(win.home)}`}
      >
        <span
          className="win-bar__seg win-bar__seg--away"
          style={{ flexGrow: win.away }}
          title={`${away.name}: ${pct(win.away)}`}
        />
        <span
          className="win-bar__seg win-bar__seg--home"
          style={{ flexGrow: win.home }}
          title={`${home.name}: ${pct(win.home)}`}
        />
      </div>
      {win.note && <p className="game-card__fine">{win.note}</p>}
    </div>
  );
}

function fmt(value) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function Compare({ section, away, home }) {
  return (
    <div className="compare" role="table" aria-label={section.title}>
      <div className="compare__legend" aria-hidden="true">
        <span>
          <span className="compare__key compare__key--away" /> {short(away)}
        </span>
        <span>
          {short(home)} <span className="compare__key compare__key--home" />
        </span>
      </div>
      {section.rows.map((r) => {
        const max = r.max || Math.max(r.away, r.home) || 1;
        const better =
          r.away === r.home ? null : (r.lower ? r.away < r.home : r.away > r.home) ? 'away' : 'home';
        const awayText = r.awayText ?? fmt(r.away);
        const homeText = r.homeText ?? fmt(r.home);
        return (
          <div className="compare__row" role="row" key={r.label}>
            <span
              className={`compare__val compare__val--away${better === 'away' ? ' is-better' : ''}`}
              role="cell"
            >
              {awayText}
            </span>
            <span className="compare__bar compare__bar--away" aria-hidden="true">
              <span
                className="compare__fill"
                style={{ width: `${Math.min(100, (r.away / max) * 100)}%` }}
                title={`${away.name} · ${r.label}: ${awayText}`}
              />
            </span>
            <span className="compare__label" role="rowheader">
              {r.label}
            </span>
            <span className="compare__bar compare__bar--home" aria-hidden="true">
              <span
                className="compare__fill"
                style={{ width: `${Math.min(100, (r.home / max) * 100)}%` }}
                title={`${home.name} · ${r.label}: ${homeText}`}
              />
            </span>
            <span
              className={`compare__val compare__val--home${better === 'home' ? ' is-better' : ''}`}
              role="cell"
            >
              {homeText}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Table({ section }) {
  return (
    <div className="game-card__table-wrap">
      <table className="game-card__table">
        <thead>
          <tr>
            {section.columns.map((c, i) => (
              <th key={i} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {section.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => {
                const { value, tone } = cellParts(cell);
                return (
                  <td key={j} className={tone ? `is-${tone}` : undefined}>
                    {value === null || value === undefined || value === '' ? '—' : value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ section, away, home }) {
  return (
    <section className="game-card__section">
      <h3 className="game-card__section-title">{section.title}</h3>
      {section.kind === 'notes' && (
        <ul className="game-card__notes">
          {section.items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
      {section.kind === 'compare' && <Compare section={section} away={away} home={home} />}
      {section.kind === 'table' && <Table section={section} />}
      {section.note && <p className="game-card__fine">{section.note}</p>}
    </section>
  );
}

// A team's big label (its abbreviation, or its name when it has none - a
// college), then its full name and its note.
function Team({ team, side }) {
  const label = short(team);
  return (
    <div className={`game-card__team game-card__team--${side}`}>
      <span className={`game-card__abbr${label.length > 5 ? ' game-card__abbr--long' : ''}`}>{label}</span>
      {team.abbr && <span className="game-card__name">{team.name}</span>}
      {team.note && <span className="game-card__team-note">{team.note}</span>}
    </div>
  );
}

function Body({ game }) {
  const { away, home, score, win } = game;
  return (
    <>
      <div className="game-card__matchup">
        <Team team={away} side="away" />
        <div className="game-card__score">
          {score && (
            <>
              <span className="game-card__score-nums">
                {score.away} <span className="game-card__dash">–</span> {score.home}
              </span>
              {score.label && <span className="game-card__score-label">{score.label}</span>}
            </>
          )}
        </div>
        <Team team={home} side="home" />
      </div>
      {win && <WinBar win={win} away={away} home={home} />}
      {(game.sections ?? []).map((section, i) => (
        <Section key={i} section={section} away={away} home={home} />
      ))}
    </>
  );
}

export default function GameCard({ status, game, error, onClose }) {
  const dialog = useRef(null);

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  return (
    <dialog
      ref={dialog}
      className="game-card"
      aria-labelledby="game-card-title"
      onClose={onClose}
      // A click on the backdrop (the dialog element itself) closes it.
      onClick={(e) => e.target === dialog.current && dialog.current.close()}
    >
      <div className="game-card__inner">
        <div className="game-card__head">
          <span id="game-card-title">
            {game ? `${game.title}${game.subtitle ? ` · ${game.subtitle}` : ''}` : 'Game'}
          </span>
          <button type="button" className="game-card__close" onClick={() => dialog.current?.close()}>
            Close
          </button>
        </div>
        <div className="game-card__body">
          {status === 'loading' && <p className="game-card__state">Loading…</p>}
          {status === 'error' && (
            <p className="game-card__state game-card__state--error">
              Couldn’t load this game{error?.message ? ` — ${error.message}` : ''}.
            </p>
          )}
          {status === 'missing' && <p className="game-card__state">No card for this game yet.</p>}
          {status === 'ready' && game && <Body game={game} />}
        </div>
      </div>
    </dialog>
  );
}
