import { STATUS_META, VIEW_TYPES } from '../config.js';
import useProjections from '../hooks/useProjections.js';
import useWeeklyProjections from '../hooks/useWeeklyProjections.js';
import ProjectionTable from './ProjectionTable.jsx';
import StatTiles from './StatTiles.jsx';

// Shared async-state renderer for one table of model output.
function SectionBody({ status, data, error }) {
  if (status === 'loading') {
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
  if (status === 'ready' && data) {
    return (
      <>
        <StatTiles summary={data.summary} />
        {data.subtitle && <p className="sport-section__meta">{data.subtitle}</p>}
        <ProjectionTable
          columns={data.columns}
          rows={data.rows}
          groupBy={data.groupBy}
        />
        {data.updated && (
          <p className="sport-section__updated">Updated {data.updated}</p>
        )}
      </>
    );
  }
  return null;
}

// One view's tab panel on a sport page. Live views load and render their
// model's table; weekly views additionally get a week dropdown driven by the
// manifest. Non-live views show the status badge. This is the single seam a
// new model plugs into — no per-sport rendering code.
export default function ProjectionSection({ sportId, view }) {
  const meta = VIEW_TYPES[view.type] ?? { label: view.type, blurb: '' };
  const isLive = view.status === 'live';
  const isWeekly = Boolean(meta.weekly);

  // Hooks run unconditionally (React rules); null ids keep the idle one inert.
  const single = useProjections(
    isLive && !isWeekly ? sportId : null,
    isLive && !isWeekly ? view.type : null
  );
  const weekly = useWeeklyProjections(
    isLive && isWeekly ? sportId : null,
    isLive && isWeekly ? view.type : null
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

      {isLive && isWeekly && (
        <>
          {weekly.indexStatus === 'loading' && (
            <p className="sport-section__state">Loading weeks…</p>
          )}
          {weekly.indexStatus === 'error' && (
            <p className="sport-section__state sport-section__state--error">
              Couldn’t load the week list
              {weekly.indexError?.message ? ` — ${weekly.indexError.message}` : ''}.
            </p>
          )}
          {weekly.indexStatus === 'empty' && (
            <p className="sport-section__state">No weeks published yet.</p>
          )}
          {weekly.indexStatus === 'ready' && (
            <>
              <StatTiles summary={weekly.manifest.summary} />
              <select
                className="week-select"
                aria-label="Select week"
                value={weekly.selected ?? ''}
                onChange={(e) => weekly.setSelected(e.target.value)}
              >
                {weekly.manifest.weeks.map((w) => (
                  <option key={w.file} value={w.file}>
                    {w.label}
                  </option>
                ))}
              </select>
              <SectionBody
                status={weekly.weekStatus}
                data={weekly.data}
                error={weekly.weekError}
              />
            </>
          )}
        </>
      )}

      {isLive && !isWeekly && (
        <SectionBody status={single.status} data={single.data} error={single.error} />
      )}
    </article>
  );
}
