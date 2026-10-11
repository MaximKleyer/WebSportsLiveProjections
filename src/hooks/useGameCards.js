import { useCallback, useEffect, useRef, useState } from 'react';
import { loadDetails } from '../data/loadProjections.js';
import { useDataVersion } from '../desktop/DesktopProvider.jsx';

// The game card a table row opens. `file` is the details file the table names
// (null when it has none: rows then aren't clickable). It is fetched on the
// first click and kept for the next ones — until the sport is re-exported in
// the desktop app.
//
// Returns { open, show(id), close() }; open is null or
//   { id, status: 'loading' | 'ready' | 'missing' | 'error', game, error }
export default function useGameCards(sportId, viewType, file) {
  const version = useDataVersion(sportId);
  const cache = useRef(new Map()); // details file -> Promise of its games
  const [open, setOpen] = useState(null);

  useEffect(() => {
    cache.current.clear();
  }, [version]);

  const show = useCallback(
    (id) => {
      if (!file || !id) return;
      setOpen({ id, status: 'loading', game: null, error: null });
      let games = cache.current.get(file);
      if (!games) {
        games = loadDetails(sportId, viewType, file).then((d) => d?.games ?? {});
        cache.current.set(file, games);
      }
      games
        .then((all) =>
          setOpen((o) =>
            o?.id === id
              ? { id, status: all[id] ? 'ready' : 'missing', game: all[id] ?? null, error: null }
              : o
          )
        )
        .catch((error) => {
          cache.current.delete(file); // try again on the next click
          setOpen((o) => (o?.id === id ? { id, status: 'error', game: null, error } : o));
        });
    },
    [sportId, viewType, file]
  );

  const close = useCallback(() => setOpen(null), []);
  return { open, show, close };
}
