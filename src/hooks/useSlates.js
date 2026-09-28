import { useEffect, useState } from 'react';
import { loadSlate, loadSlateIndex } from '../data/loadProjections.js';

// A slate view's data: load its manifest (index.json), track the selected
// slate — a football week, an MLB day — defaulting to the manifest's
// `latest`, and load that slate's table. Pass null ids to stay idle (e.g.
// for a view that isn't live).
//
// While the next slate loads, the previous table stays in `data` (status
// 'loading') so the page doesn't jump and the table keeps its sort/search.
//
// Returns:
//   indexStatus: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
//   manifest, indexError
//   selected (slate file), setSelected
//   status: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
//   data, error
export default function useSlates(sportId, viewType) {
  const [index, setIndex] = useState({ status: 'idle', manifest: null, error: null });
  const [selected, setSelected] = useState(null);
  const [slate, setSlate] = useState({ status: 'idle', data: null, error: null });

  // Load the manifest, then default the selection to its latest slate.
  useEffect(() => {
    if (!sportId || !viewType) {
      setIndex({ status: 'idle', manifest: null, error: null });
      setSelected(null);
      return;
    }

    let cancelled = false;
    setIndex({ status: 'loading', manifest: null, error: null });
    setSelected(null);

    loadSlateIndex(sportId, viewType)
      .then((manifest) => {
        if (cancelled) return;
        const slates = Array.isArray(manifest?.slates) ? manifest.slates : [];
        if (slates.length === 0) {
          setIndex({ status: 'empty', manifest, error: null });
          return;
        }
        setIndex({ status: 'ready', manifest, error: null });
        setSelected(manifest.latest ?? slates[slates.length - 1].file);
      })
      .catch((error) => {
        if (!cancelled) setIndex({ status: 'error', manifest: null, error });
      });

    return () => {
      cancelled = true;
    };
  }, [sportId, viewType]);

  // Load the selected slate's table.
  useEffect(() => {
    if (!sportId || !viewType || !selected) {
      setSlate({ status: 'idle', data: null, error: null });
      return;
    }

    let cancelled = false;
    setSlate((prev) => ({ status: 'loading', data: prev.data, error: null }));

    loadSlate(sportId, viewType, selected)
      .then((data) => {
        if (cancelled) return;
        const isEmpty = !data || !Array.isArray(data.rows) || data.rows.length === 0;
        setSlate({ status: isEmpty ? 'empty' : 'ready', data, error: null });
      })
      .catch((error) => {
        if (!cancelled) setSlate({ status: 'error', data: null, error });
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
    status: slate.status,
    data: slate.data,
    error: slate.error,
  };
}
