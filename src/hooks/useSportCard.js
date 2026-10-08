import { useEffect, useState } from 'react';
import { loadCard } from '../data/loadProjections.js';
import { useDataVersion } from '../desktop/DesktopProvider.jsx';

// A sport's landing-card line (data/<sport>/card.json): what its model has out
// now, when it last ran, and its record. Pass a null id to stay idle (a sport
// that isn't live has no card file). In the desktop app it re-loads whenever
// the sport is re-exported, keeping the current line up meanwhile.
//
// Returns: { status: 'idle' | 'loading' | 'ready' | 'error', data }
export default function useSportCard(sportId) {
  const version = useDataVersion(sportId);
  const [state, setState] = useState({ status: 'idle', data: null, id: null });

  useEffect(() => {
    if (!sportId) {
      setState({ status: 'idle', data: null, id: null });
      return;
    }

    let cancelled = false;
    setState((prev) => (prev.id === sportId ? prev : { status: 'loading', data: null, id: sportId }));

    loadCard(sportId)
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data, id: sportId });
      })
      .catch(() => {
        if (!cancelled) {
          setState((prev) => (prev.id === sportId && prev.data ? prev : { status: 'error', data: null, id: sportId }));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [sportId, version]);

  return state;
}
