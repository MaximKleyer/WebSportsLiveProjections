import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { loadSlate, loadSlateIndex } from '../data/loadProjections.js';
import { useDataVersion } from '../desktop/DesktopProvider.jsx';

// A slate view's data: load its manifest (index.json), work out the selected
// slate — a football week, an MLB day — and load that slate's table. Pass
// null ids to stay idle (e.g. for a view that isn't live).
//
// The selection lives in the URL under the manifest's unit
// (#/cfb?tab=games&week=2026-w03), so any week can be linked to; a missing or
// unknown id falls back to the manifest's `latest`.
//
// While the next slate loads, the previous table stays in `data` (status
// 'loading') so the page doesn't jump and the table keeps its sort/search.
// In the desktop app both are re-fetched whenever the sport is re-exported.
//
// Returns:
//   indexStatus: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
//   manifest, indexError
//   selected (slate id), select(id)
//   status: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
//   data, error
export default function useSlates(sportId, viewType) {
  const version = useDataVersion(sportId);
  const [params, setParams] = useSearchParams();
  const [index, setIndex] = useState({ status: 'idle', manifest: null, error: null, key: null });
  const [slate, setSlate] = useState({ status: 'idle', data: null, error: null, file: null });
  const key = sportId && viewType ? `${sportId}/${viewType}` : null;

  // Load the manifest (a refresh keeps the current one up meanwhile).
  useEffect(() => {
    if (!key) {
      setIndex({ status: 'idle', manifest: null, error: null, key: null });
      return;
    }

    let cancelled = false;
    setIndex((prev) => (prev.key === key ? prev : { status: 'loading', manifest: null, error: null, key }));

    loadSlateIndex(sportId, viewType)
      .then((manifest) => {
        if (cancelled) return;
        const hasSlates = Array.isArray(manifest?.slates) && manifest.slates.length > 0;
        setIndex({ status: hasSlates ? 'ready' : 'empty', manifest, error: null, key });
      })
      .catch((error) => {
        if (cancelled) return;
        setIndex((prev) =>
          prev.key === key && prev.manifest ? prev : { status: 'error', manifest: null, error, key }
        );
      });

    return () => {
      cancelled = true;
    };
  }, [key, sportId, viewType, version]);

  const manifest = index.manifest;
  const slates = index.status === 'ready' ? manifest.slates : [];
  const unit = manifest?.unit ?? 'week';
  const current =
    slates.find((s) => s.id === params.get(unit)) ??
    slates.find((s) => s.file === manifest.latest) ??
    slates[slates.length - 1] ??
    null;
  const file = current?.file ?? null;

  // Replace, not push: flipping through weeks shouldn't fill the back button.
  const select = (id) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(unit, id);
        return next;
      },
      { replace: true }
    );

  // Load the selected slate's table.
  useEffect(() => {
    if (!sportId || !viewType || !file) {
      setSlate({ status: 'idle', data: null, error: null, file: null });
      return;
    }

    let cancelled = false;
    setSlate((prev) => ({ status: 'loading', data: prev.data, error: null, file: prev.file }));

    loadSlate(sportId, viewType, file)
      .then((data) => {
        if (cancelled) return;
        const isEmpty = !data || !Array.isArray(data.rows) || data.rows.length === 0;
        setSlate({ status: isEmpty ? 'empty' : 'ready', data, error: null, file });
      })
      .catch((error) => {
        if (cancelled) return;
        // A failed refresh of the same slate keeps what's on screen.
        setSlate((prev) =>
          prev.file === file && prev.data
            ? { ...prev, status: 'ready' }
            : { status: 'error', data: null, error, file }
        );
      });

    return () => {
      cancelled = true;
    };
  }, [sportId, viewType, file, version]);

  return {
    indexStatus: index.status,
    manifest,
    indexError: index.error,
    selected: current?.id ?? null,
    select,
    status: slate.status,
    data: slate.data,
    error: slate.error,
  };
}
