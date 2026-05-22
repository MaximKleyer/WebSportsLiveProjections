import SportCard from '../components/SportCard.jsx';
import { BRAND, SPORTS } from '../config.js';

export default function Landing() {
  return (
    <div className="landing">
      <section className="hero">
        <div className="hero__kicker">
          <span className="hero__kicker-dot" />
          <span>EST. 2026 · NEW YORK</span>
        </div>
        <h1 className="hero__title">
          PROJECTIONS<span className="hero__title-period">.</span>
        </h1>
        <p className="hero__lede">
          {BRAND.tagline}. Game-by-game, player-by-player, season-long.
          One desk, every league.
        </p>
      </section>

      <section className="grid-section" aria-label="Sports">
        <div className="grid-section__head">
          <span className="grid-section__label">SECTIONS</span>
          <span className="grid-section__count">{SPORTS.length} LEAGUES</span>
        </div>

        <div className="sport-grid">
          {SPORTS.map((sport, i) => (
            <SportCard key={sport.id} sport={sport} index={i} />
          ))}
        </div>
      </section>
    </div>
  );
}
