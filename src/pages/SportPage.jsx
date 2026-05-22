import { Link } from 'react-router-dom';
import { STATUS_META } from '../config.js';

export default function SportPage({ sport }) {
  const status = STATUS_META[sport.status];

  return (
    <div className="sport-page" style={{ '--accent': sport.accent }}>
      <div className="sport-page__crumb">
        <Link to="/">← BACK</Link>
      </div>

      <header className="sport-page__head">
        <div className="sport-page__meta">
          <span>{sport.year}</span>
          <span className="sport-page__sep">·</span>
          <span className="sport-page__status">
            <span className="sport-page__status-dot" />
            {status.label}
          </span>
        </div>
        <h1 className="sport-page__title">{sport.name}</h1>
        <p className="sport-page__subtitle">{sport.subtitle}</p>
      </header>

      <section className="sport-page__sections">
        <article className="sport-section">
          <span className="sport-section__num">01</span>
          <h2 className="sport-section__title">Weekly Game Projections</h2>
          <p className="sport-section__body">
            Score and win-probability projections for every scheduled game this week.
          </p>
          <span className="sport-section__tag">COMING SOON</span>
        </article>

        <article className="sport-section">
          <span className="sport-section__num">02</span>
          <h2 className="sport-section__title">Player Projections</h2>
          <p className="sport-section__body">
            Per-player stat lines with confidence intervals and matchup adjustments.
          </p>
          <span className="sport-section__tag">COMING SOON</span>
        </article>

        <article className="sport-section">
          <span className="sport-section__num">03</span>
          <h2 className="sport-section__title">Full-Season Projections</h2>
          <p className="sport-section__body">
            Team standings, playoff odds, and individual award tracking through the season.
          </p>
          <span className="sport-section__tag">COMING SOON</span>
        </article>
      </section>
    </div>
  );
}
