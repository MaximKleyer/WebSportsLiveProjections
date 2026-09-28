// Headline numbers above a table — e.g. a model's season-to-date record on
// the Results tab. Data-driven like the tables; any table JSON or weekly
// manifest may carry one:
//   summary: { title?, stats: [{ label, value, detail?, tone? }], note? }
// tone ('good' | 'bad' | 'muted') colors the tile's detail line.

export default function StatTiles({ summary }) {
  const stats = Array.isArray(summary?.stats) ? summary.stats : [];
  if (stats.length === 0) return null;

  return (
    <div className="stat-tiles">
      {summary.title && <p className="stat-tiles__title">{summary.title}</p>}
      <div className="stat-tiles__grid">
        {stats.map((s) => (
          <div
            key={s.label}
            className={`stat-tile${s.tone ? ` stat-tile--${s.tone}` : ''}`}
          >
            <span className="stat-tile__label">{s.label}</span>
            <span className="stat-tile__value">{s.value}</span>
            {s.detail && <span className="stat-tile__detail">{s.detail}</span>}
          </div>
        ))}
      </div>
      {summary.note && <p className="stat-tiles__note">{summary.note}</p>}
    </div>
  );
}
