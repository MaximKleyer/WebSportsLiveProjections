import { Link, NavLink } from 'react-router-dom';
import { BRAND, SPORTS, GITHUB_USERNAME } from '../config.js';

export default function Header() {
  const githubUrl = `https://${GITHUB_USERNAME}.github.io/`;

  return (
    <header className="site-header">
      <div className="site-header__inner">
        <Link to="/" className="brand">
          <span className="brand__mark">★</span>
          <span className="brand__name">{BRAND.wordmark}</span>
        </Link>

        <nav className="site-nav" aria-label="Sports">
          {SPORTS.map((sport) => (
            <NavLink
              key={sport.id}
              to={`/${sport.id}`}
              className={({ isActive }) =>
                `site-nav__link${isActive ? ' is-active' : ''}`
              }
              style={{ '--sport-accent': sport.accent }}
            >
              {sport.name === 'COLLEGE FOOTBALL'
                ? 'CFB'
                : sport.name === 'COLLEGE BASKETBALL'
                ? 'CBB'
                : sport.name}
            </NavLink>
          ))}
        </nav>

        <a
          href={githubUrl}
          className="github-link"
          target="_blank"
          rel="noopener noreferrer"
        >
          GITHUB.IO
          <span aria-hidden="true" className="github-link__arrow">↗</span>
        </a>
      </div>
    </header>
  );
}
