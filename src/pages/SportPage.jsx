import { Link, useSearchParams } from 'react-router-dom';
import { STATUS_META, viewMeta } from '../config.js';
import useDocumentTitle from '../hooks/useDocumentTitle.js';
import ProjectionSection from '../components/ProjectionSection.jsx';

export default function SportPage({ sport }) {
  const status = STATUS_META[sport.status] ?? { label: (sport.status ?? 'unknown').toUpperCase() };
  useDocumentTitle(`${sport.name} · Sports Models`);

  // One tab per view. The open tab lives in the URL (#/cfb?tab=results) so any
  // view can be linked to; no or unknown tab opens the first live view.
  const [params, setParams] = useSearchParams();
  const viewsList = sport.views ?? [];
  const defaultView = viewsList.find((v) => v.status === 'live') ?? viewsList[0] ?? null;
  const activeView = viewsList.find((v) => v.type === params.get('tab')) ?? defaultView;
  // Replace, not push, so tabs don't fill the back button; switching tabs
  // also drops the old tab's week.
  const openTab = (type) =>
    setParams(type === defaultView?.type ? {} : { tab: type }, { replace: true });

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
              const meta = viewMeta(v);
              const isActive = activeView?.type === v.type;
              return (
                <button
                  key={v.type}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  className={`view-tabs__tab${isActive ? ' is-active' : ''}`}
                  onClick={() => openTab(v.type)}
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
