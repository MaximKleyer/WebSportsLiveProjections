import { Link, NavLink } from 'react-router-dom';
import { BRAND, SPORTS, GITHUB_USERNAME, REPO_NAME } from '../config.js';

export default function Header() {
  // Link to the source repo (never 404s). Swap to the live-site or a
  // portfolio URL here if you'd rather the header point elsewhere.
  const repoUrl = `https://github.com/${GITHUB_USERNAME}/${REPO_NAME}`;

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
          href={repoUrl}
          className="github-link"
          target="_blank"
          rel="noopener noreferrer"
        >
          GITHUB
          <span aria-hidden="true" className="github-link__arrow">↗</span>
        </a>
      </div>
    </header>
  );
}
