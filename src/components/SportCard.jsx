import { Link } from 'react-router-dom';
import { STATUS_META } from '../config.js';
import useSportCard from '../hooks/useSportCard.js';

// 'Sep 25' from '2026-09-25', read as a local date (no UTC day shift).
function shortDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function SportCard({ sport, index }) {
  const status = STATUS_META[sport.status] ?? { label: (sport.status ?? 'unknown').toUpperCase() };
  const isReady = sport.status === 'live';
  // Live sports show what their model has out now (data/<sport>/card.json).
  const card = useSportCard(isReady ? sport.id : null);
  const live = card.data;

  return (
    <Link
      to={`/${sport.id}`}
      className={`sport-card sport-card--${sport.status}`}
      style={{
        '--accent': sport.accent,
        '--delay': `${index * 60}ms`,
      }}
    >
      <div className="sport-card__top">
        <span className="sport-card__year">{sport.year}</span>
        <span className="sport-card__status">
          <span className="sport-card__status-dot" />
          {status.label}
        </span>
      </div>

      <h2 className="sport-card__name">{sport.name}</h2>
      <p className="sport-card__subtitle">{sport.subtitle}</p>

      {/* Space is reserved while card.json loads so the grid doesn't jump;
          no card file just means no live line. */}
      {isReady && card.status !== 'error' && (
        <div className="sport-card__live" aria-busy={card.status === 'loading'}>
          {live && (
            <>
              <p className="sport-card__headline">
                {live.headline}
                {live.updated && ` · Updated ${shortDate(live.updated)}`}
              </p>
              {live.stats?.length > 0 && (
                <dl className="sport-card__stats">
                  {live.stats.map((s) => (
                    <div
                      key={s.label}
                      className={`sport-card__stat${s.tone ? ` sport-card__stat--${s.tone}` : ''}`}
                    >
                      <dt>{s.label}</dt>
                      <dd>
                        <span className="sport-card__stat-value">{s.value}</span>
                        {s.detail && <span className="sport-card__stat-detail">{s.detail}</span>}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </>
          )}
        </div>
      )}

      <div className="sport-card__cta">
        <span>{isReady ? 'OPEN MODEL' : 'PREVIEW'}</span>
        <span aria-hidden="true">→</span>
      </div>

      <div className="sport-card__accent-bar" aria-hidden="true" />
    </Link>
  );
}
