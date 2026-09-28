import { STATUS_META, viewMeta } from '../config.js';
import useProjections from '../hooks/useProjections.js';
import useSlates from '../hooks/useSlates.js';
import ProjectionTable from './ProjectionTable.jsx';
import StatTiles from './StatTiles.jsx';

// Shared async-state renderer for one table of model output. While a slate
// view loads its next table, the previous one stays up (dimmed), so the page
// doesn't jump and the table keeps its sort / search / filters.
function SectionBody({ status, data, error }) {
  const hasRows = Array.isArray(data?.rows) && data.rows.length > 0;
  if (status === 'loading' && !hasRows) {
    return <p className="sport-section__state">Loading…</p>;
  }
  if (status === 'error') {
    return (
      <p className="sport-section__state sport-section__state--error">
        Couldn’t load this model{error?.message ? ` — ${error.message}` : ''}.
      </p>
    );
  }
  if (status === 'empty') {
    return <p className="sport-section__state">No projections available yet.</p>;
  }
  if ((status === 'ready' || status === 'loading') && hasRows) {
    const loading = status === 'loading';
    return (
      <div className={`sport-section__data${loading ? ' is-loading' : ''}`} aria-busy={loading}>
        <StatTiles summary={data.summary} />
        {data.subtitle && <p className="sport-section__meta">{data.subtitle}</p>}
        <ProjectionTable
          columns={data.columns}
          rows={data.rows}
          groupBy={data.groupBy}
          search={data.search}
          filters={data.filters}
        />
        {data.updated && (
          <p className="sport-section__updated">Updated {data.updated}</p>
        )}
      </div>
    );
  }
  return null;
}

// One view's tab panel on a sport page. Live views load and render their
// model's table; slate views (a week, a day) add a dropdown driven by their
// manifest. Non-live views show the status badge. This is the single seam a
// new model plugs into — no per-sport rendering code.
export default function ProjectionSection({ sportId, view }) {
  const meta = viewMeta(view);
  const isLive = view.status === 'live';
  const hasSlates = Boolean(meta.slates);

  // Hooks run unconditionally (React rules); null ids keep the idle one inert.
  const single = useProjections(
    isLive && !hasSlates ? sportId : null,
    isLive && !hasSlates ? view.type : null
  );
  const slate = useSlates(
    isLive && hasSlates ? sportId : null,
    isLive && hasSlates ? view.type : null
  );

  return (
    <article className="sport-section">
      <h2 className="sport-section__title">{meta.label}</h2>
      {meta.blurb && <p className="sport-section__body">{meta.blurb}</p>}

      {!isLive && (
        <span className="sport-section__tag">
          {STATUS_META[view.status]?.label ?? 'COMING SOON'}
        </span>
      )}

      {isLive && hasSlates && (
        <>
          {slate.indexStatus === 'loading' && (
            <p className="sport-section__state">Loading…</p>
          )}
          {slate.indexStatus === 'error' && (
            <p className="sport-section__state sport-section__state--error">
              Couldn’t load this view
              {slate.indexError?.message ? ` — ${slate.indexError.message}` : ''}.
            </p>
          )}
          {slate.indexStatus === 'empty' && (
            <p className="sport-section__state">Nothing published yet.</p>
          )}
          {slate.indexStatus === 'ready' && (
            <>
              <StatTiles summary={slate.manifest.summary} />
              <select
                className="slate-select"
                aria-label={`Select ${slate.manifest.unit ?? 'week'}`}
                value={slate.selected ?? ''}
                onChange={(e) => slate.setSelected(e.target.value)}
              >
                {slate.manifest.slates.map((s) => (
                  <option key={s.file} value={s.file}>
                    {s.label}
                  </option>
                ))}
              </select>
              <SectionBody status={slate.status} data={slate.data} error={slate.error} />
            </>
          )}
        </>
      )}

      {isLive && !hasSlates && (
        <SectionBody status={single.status} data={single.data} error={single.error} />
      )}
    </article>
  );
}
