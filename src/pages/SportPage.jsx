import { useState } from 'react';
import { Link } from 'react-router-dom';
import { STATUS_META, VIEW_TYPES } from '../config.js';
import useDocumentTitle from '../hooks/useDocumentTitle.js';
import ProjectionSection from '../components/ProjectionSection.jsx';

export default function SportPage({ sport }) {
  const status = STATUS_META[sport.status] ?? { label: (sport.status ?? 'unknown').toUpperCase() };
  useDocumentTitle(`${sport.name} · Sports Models`);

  // One tab per view; open on the first live one.
  const viewsList = sport.views ?? [];
  const defaultType =
    (viewsList.find((v) => v.status === 'live') ?? viewsList[0])?.type ?? null;
  const [activeType, setActiveType] = useState(defaultType);
  const activeView = viewsList.find((v) => v.type === activeType) ?? viewsList[0] ?? null;

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

      {viewsList.length > 0 && (
        <>
          <div className="view-tabs" role="tablist" aria-label="Projection views">
            {viewsList.map((v) => {
              const meta = VIEW_TYPES[v.type] ?? {};
              const isActive = activeView?.type === v.type;
              return (
                <button
                  key={v.type}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  className={`view-tabs__tab${isActive ? ' is-active' : ''}`}
                  onClick={() => setActiveType(v.type)}
                >
                  {meta.short ?? v.type}
                </button>
              );
            })}
          </div>

          {activeView && (
            <div role="tabpanel">
              <ProjectionSection
                key={activeView.type}
                sportId={sport.id}
                view={activeView}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
