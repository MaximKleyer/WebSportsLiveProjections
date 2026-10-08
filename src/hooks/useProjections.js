import { useEffect, useState } from 'react';
import { loadProjections } from '../data/loadProjections.js';
import { useDataVersion } from '../desktop/DesktopProvider.jsx';

// Load model output for a sport/view, exposing an explicit status machine so
// the UI can render loading / error / empty / ready states. Pass null ids to
// stay idle (e.g. for a view that isn't live yet — no fetch is attempted).
//
// In the desktop app the data is re-fetched whenever the sport is
// re-exported; the current table stays up until the new one arrives.
//
// Returns: { status: 'idle' | 'loading' | 'ready' | 'empty' | 'error', data, error }
export default function useProjections(sportId, viewType) {
  const version = useDataVersion(sportId);
  const [state, setState] = useState({ status: 'idle', data: null, error: null, key: null });
  const key = sportId && viewType ? `${sportId}/${viewType}` : null;

  useEffect(() => {
    if (!key) {
      setState({ status: 'idle', data: null, error: null, key: null });
      return;
    }

    let cancelled = false;
    setState((prev) => (prev.key === key ? prev : { status: 'loading', data: null, error: null, key }));

    loadProjections(sportId, viewType)
      .then((data) => {
        if (cancelled) return;
        const isEmpty = !data || !Array.isArray(data.rows) || data.rows.length === 0;
        setState({ status: isEmpty ? 'empty' : 'ready', data, error: null, key });
      })
      .catch((error) => {
        if (cancelled) return;
        // A failed refresh keeps what's on screen.
        setState((prev) => (prev.key === key && prev.data ? prev : { status: 'error', data: null, error, key }));
      });

    // Ignore a stale response if the ids change or the component unmounts.
    return () => {
      cancelled = true;
    };
  }, [key, sportId, viewType, version]);

  return state;
}
