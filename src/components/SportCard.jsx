import { Link } from 'react-router-dom';
import { STATUS_META } from '../config.js';

export default function SportCard({ sport, index }) {
  const status = STATUS_META[sport.status];
  const isReady = sport.status === 'live';

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

      <div className="sport-card__cta">
        <span>{isReady ? 'OPEN MODEL' : 'PREVIEW'}</span>
        <span aria-hidden="true">→</span>
      </div>

      <div className="sport-card__accent-bar" aria-hidden="true" />
    </Link>
  );
}
