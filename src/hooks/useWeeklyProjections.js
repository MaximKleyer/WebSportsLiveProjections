import { useEffect, useState } from 'react';
import { loadWeeklyIndex, loadWeeklyWeek } from '../data/loadProjections.js';

// Weekly model output: load the week manifest, track the selected week
// (defaulting to the latest published), and load that week's table.
// Pass null ids to stay idle (e.g. for a view that isn't live/weekly).
//
// Returns:
//   indexStatus: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
//   manifest, indexError
//   selected (week file), setSelected
//   weekStatus: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
//   data, weekError
export default function useWeeklyProjections(sportId, viewType) {
  const [index, setIndex] = useState({ status: 'idle', manifest: null, error: null });
  const [selected, setSelected] = useState(null);
  const [week, setWeek] = useState({ status: 'idle', data: null, error: null });

  // Load the manifest, then default the selection to the latest week.
  useEffect(() => {
    if (!sportId || !viewType) {
      setIndex({ status: 'idle', manifest: null, error: null });
      setSelected(null);
      return;
    }

    let cancelled = false;
    setIndex({ status: 'loading', manifest: null, error: null });
    setSelected(null);

    loadWeeklyIndex(sportId, viewType)
      .then((manifest) => {
        if (cancelled) return;
        const weeks = Array.isArray(manifest?.weeks) ? manifest.weeks : [];
        if (weeks.length === 0) {
          setIndex({ status: 'empty', manifest, error: null });
          return;
        }
        setIndex({ status: 'ready', manifest, error: null });
        setSelected(manifest.latest ?? weeks[weeks.length - 1].file);
      })
      .catch((error) => {
        if (!cancelled) setIndex({ status: 'error', manifest: null, error });
      });

    return () => {
      cancelled = true;
    };
  }, [sportId, viewType]);

  // Load the selected week's table.
  useEffect(() => {
    if (!sportId || !viewType || !selected) {
      setWeek({ status: 'idle', data: null, error: null });
      return;
    }

    let cancelled = false;
    setWeek({ status: 'loading', data: null, error: null });

    loadWeeklyWeek(sportId, viewType, selected)
      .then((data) => {
        if (cancelled) return;
        const isEmpty = !data || !Array.isArray(data.rows) || data.rows.length === 0;
        setWeek({ status: isEmpty ? 'empty' : 'ready', data, error: null });
      })
      .catch((error) => {
        if (!cancelled) setWeek({ status: 'error', data: null, error });
      });

    return () => {
      cancelled = true;
    };
  }, [sportId, viewType, selected]);

  return {
    indexStatus: index.status,
    manifest: index.manifest,
    indexError: index.error,
    selected,
    setSelected,
    weekStatus: week.status,
    data: week.data,
    weekError: week.error,
  };
}
