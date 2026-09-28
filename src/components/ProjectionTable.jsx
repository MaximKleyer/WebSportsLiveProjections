import { useState } from 'react';

// Generic, data-driven table for any model's output. Every view renders
// through this one component so the six sports never drift in look/behavior.
//   columns  [{ key, label, align?: 'left'|'right'|'center',
//              format?: 'percent'|'number', sortable? }]
//   rows     [{ <key>: cell }] — a cell is a plain value, or { v, tone?, sort? }:
//            tone ('good' | 'bad' | 'muted') colors it (a graded pick: hit /
//            miss / push); sort is what the column orders by when the shown
//            text isn't (a kickoff time, an edge size)
//   groupBy  a row field (e.g. "division") — one sub-table per distinct value,
//            in row order, under an accent-colored header; rows carry the
//            field, it needs no column
//   search   placeholder text; adds a box that filters rows by any cell text
//            or group name
//   filters  [{ field, label }] — toggles keeping rows whose row[field] is
//            truthy (e.g. ★ plays only)
// Sortable headers cycle: best first (high numbers / A→Z text), reversed,
// then back to the model's own order. Grouped tables sort within each group.

function splitCell(cell) {
  return cell !== null && typeof cell === 'object' ? [cell.v, cell.tone] : [cell, null];
}

function formatCell(value, format) {
  if (value === null || value === undefined || value === '') return '—';
  if (format === 'percent') return `${Math.round(Number(value) * 100)}%`;
  if (format === 'number') {
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  }
  return value;
}

const isBlank = (x) => x === null || x === undefined || x === '';

// What a column orders by: the cell's `sort` when it has one, else its value.
function sortKey(cell) {
  if (cell !== null && typeof cell === 'object') return 'sort' in cell ? cell.sort : cell.v;
  return cell;
}

function compare(a, b) {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

// Numbers read best-first (high → low); text reads A → Z.
function firstDirection(rows, key) {
  const sample = rows.map((r) => sortKey(r[key])).find((x) => !isBlank(x));
  return typeof sample === 'number' ? 'desc' : 'asc';
}

function sortRows(rows, key, dir) {
  const sign = dir === 'asc' ? 1 : -1;
  // Stable sort: ties keep the model's order. Blanks always go last.
  return [...rows].sort((r1, r2) => {
    const a = sortKey(r1[key]);
    const b = sortKey(r2[key]);
    if (isBlank(a) || isBlank(b)) return isBlank(a) - isBlank(b);
    return sign * compare(a, b);
  });
}

function searchText(row, columns, groupBy) {
  const parts = columns.map((c) => splitCell(row[c.key])[0]);
  if (groupBy) parts.push(row[groupBy]);
  return parts.filter((p) => !isBlank(p)).join(' ').toLowerCase();
}

export default function ProjectionTable({
  columns = [],
  rows = [],
  groupBy = null,
  search = null,
  filters = [],
}) {
  const [sort, setSort] = useState(null); // { key, dir }, or null = the model's order
  const [query, setQuery] = useState('');
  const [active, setActive] = useState({}); // { [filter field]: true }

  // This state outlives a slate switch (the table stays mounted), so only
  // what the current table declares applies.
  const sortCol = columns.find((c) => c.sortable && c.key === sort?.key);
  const onFilters = filters.filter((f) => active[f.field]);
  const q = search ? query.trim().toLowerCase() : '';
  const narrowed = onFilters.length > 0 || q !== '';

  const kept = rows.filter(
    (row) =>
      onFilters.every((f) => row[f.field]) &&
      (!q || searchText(row, columns, groupBy).includes(q))
  );
  const visible = sortCol ? sortRows(kept, sortCol.key, sort.dir) : kept;
  const rowIndex = new Map(rows.map((row, i) => [row, i])); // stable React keys

  const toggleSort = (key) => {
    const first = firstDirection(rows, key);
    setSort((s) => {
      if (s?.key !== key) return { key, dir: first };
      if (s.dir === first) return { key, dir: first === 'asc' ? 'desc' : 'asc' };
      return null; // third click: back to the model's order
    });
  };

  const renderRow = (row) => (
    <tr key={rowIndex.get(row)} className="proj-table__row">
      {columns.map((col) => {
        const [value, tone] = splitCell(row[col.key]);
        return (
          <td
            key={col.key}
            className={`proj-table__td proj-table__cell--${col.align ?? 'left'}${
              tone ? ` proj-table__td--${tone}` : ''
            }`}
          >
            {formatCell(value, col.format)}
          </td>
        );
      })}
    </tr>
  );

  let body;
  if (visible.length === 0) {
    body = (
      <tbody>
        <tr className="proj-table__empty">
          <td colSpan={columns.length}>No rows match.</td>
        </tr>
      </tbody>
    );
  } else if (groupBy) {
    // Groups keep the model's order (exporters pre-sort by group); rows land
    // in their group in `visible` order, so a sort applies within each group.
    const groups = new Map();
    for (const row of rows) groups.set(row[groupBy] ?? '—', []);
    for (const row of visible) groups.get(row[groupBy] ?? '—').push(row);
    body = [...groups]
      .filter(([, groupRows]) => groupRows.length > 0)
      .map(([label, groupRows]) => (
        <tbody key={label}>
          <tr className="proj-table__group">
            <td colSpan={columns.length}>{label}</td>
          </tr>
          {groupRows.map(renderRow)}
        </tbody>
      ));
  } else {
    body = <tbody>{visible.map(renderRow)}</tbody>;
  }

  return (
    <>
      {(search || filters.length > 0) && (
        <div className="table-tools">
          {search && (
            <input
              type="search"
              className="table-tools__search"
              placeholder={search}
              aria-label={search}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          {filters.map((f) => (
            <label
              key={f.field}
              className={`table-tools__toggle${active[f.field] ? ' is-on' : ''}`}
            >
              <input
                type="checkbox"
                checked={Boolean(active[f.field])}
                onChange={() => setActive((a) => ({ ...a, [f.field]: !a[f.field] }))}
              />
              {f.label}
            </label>
          ))}
          {narrowed && (
            <span className="table-tools__count">
              {visible.length} of {rows.length} shown
            </span>
          )}
        </div>
      )}
      <div className="proj-table__wrap">
        <table className="proj-table">
          <thead>
            <tr>
              {columns.map((col) => {
                const dir = sortCol?.key === col.key ? sort.dir : null;
                return (
                  <th
                    key={col.key}
                    className={`proj-table__th proj-table__cell--${col.align ?? 'left'}`}
                    scope="col"
                    aria-sort={dir ? (dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    {col.sortable ? (
                      <button
                        type="button"
                        className="proj-table__sort"
                        onClick={() => toggleSort(col.key)}
                      >
                        {col.label}
                        <span className="proj-table__sort-icon" aria-hidden="true">
                          {dir === 'asc' ? '↑' : dir === 'desc' ? '↓' : '↕'}
                        </span>
                      </button>
                    ) : (
                      col.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          {body}
        </table>
      </div>
    </>
  );
}
