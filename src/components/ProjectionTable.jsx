// Generic, data-driven table for any model's output. Every view renders
// through this one component so the six sports never drift in look/behavior.
// Columns: { key, label, align?: 'left'|'right'|'center', format?: 'percent'|'number' }
// Optional groupBy: name of a row field to group by (e.g. "division") — the
// table renders one sub-table per distinct value, in row order, with the
// value as an accent-colored group header. Rows carry the field; it does not
// need its own column.

function formatCell(value, format) {
  if (value === null || value === undefined || value === '') return '—';
  if (format === 'percent') return `${Math.round(Number(value) * 100)}%`;
  if (format === 'number') {
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  }
  return value;
}

export default function ProjectionTable({ columns = [], rows = [], groupBy = null }) {
  const renderRow = (row, i) => (
    <tr key={i} className="proj-table__row">
      {columns.map((col) => (
        <td
          key={col.key}
          className={`proj-table__td proj-table__cell--${col.align ?? 'left'}`}
        >
          {formatCell(row[col.key], col.format)}
        </td>
      ))}
    </tr>
  );

  let body;
  if (groupBy) {
    // Group rows preserving their order (exporters pre-sort by group).
    const groups = [];
    const byLabel = new Map();
    for (const row of rows) {
      const label = row[groupBy] ?? '—';
      let group = byLabel.get(label);
      if (!group) {
        group = { label, rows: [] };
        byLabel.set(label, group);
        groups.push(group);
      }
      group.rows.push(row);
    }
    body = groups.map((group) => (
      <tbody key={group.label}>
        <tr className="proj-table__group">
          <td colSpan={columns.length}>{group.label}</td>
        </tr>
        {group.rows.map(renderRow)}
      </tbody>
    ));
  } else {
    body = <tbody>{rows.map(renderRow)}</tbody>;
  }

  return (
    <div className="proj-table__wrap">
      <table className="proj-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                className={`proj-table__th proj-table__cell--${col.align ?? 'left'}`}
                scope="col"
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        {body}
      </table>
    </div>
  );
}
