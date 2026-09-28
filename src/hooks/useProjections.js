import { useEffect, useState } from 'react';
import { loadProjections } from '../data/loadProjections.js';

// Load model output for a sport/view, exposing an explicit status machine so
// the UI can render loading / error / empty / ready states. Pass null ids to
// stay idle (e.g. for a view that isn't live yet — no fetch is attempted).
//
// Returns: { status: 'idle' | 'loading' | 'ready' | 'empty' | 'error', data, error }
export default function useProjections(sportId, viewType) {
  const [state, setState] = useState({ status: 'idle', data: null, error: null });

  useEffect(() => {
    if (!sportId || !viewType) {
      setState({ status: 'idle', data: null, error: null });
      return;
    }

    let cancelled = false;
    setState({ status: 'loading', data: null, error: null });

    loadProjections(sportId, viewType)
      .then((data) => {
        if (cancelled) return;
        const isEmpty = !data || !Array.isArray(data.rows) || data.rows.length === 0;
        setState({ status: isEmpty ? 'empty' : 'ready', data, error: null });
      })
      .catch((error) => {
        if (cancelled) return;
        setState({ status: 'error', data: null, error });
      });

    // Ignore a stale response if the ids change or the component unmounts.
    return () => {
      cancelled = true;
    };
  }, [sportId, viewType]);

  return state;
}
