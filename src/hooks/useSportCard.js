import { useEffect, useState } from 'react';
import { loadCard } from '../data/loadProjections.js';

// A sport's landing-card line (data/<sport>/card.json): what its model has out
// now, when it last ran, and its record. Pass a null id to stay idle (a sport
// that isn't live has no card file).
//
// Returns: { status: 'idle' | 'loading' | 'ready' | 'error', data }
export default function useSportCard(sportId) {
  const [state, setState] = useState({ status: 'idle', data: null });

  useEffect(() => {
    if (!sportId) {
      setState({ status: 'idle', data: null });
      return;
    }

    let cancelled = false;
    setState({ status: 'loading', data: null });

    loadCard(sportId)
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data });
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error', data: null });
      });

    return () => {
      cancelled = true;
    };
  }, [sportId]);

  return state;
}
