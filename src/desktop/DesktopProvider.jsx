import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { IS_DESKTOP, bye, getStatus, ping, startRun } from './api.js';

// Inside the desktop app, the run status every page can read: what each
// model is doing, and a per-sport data version that ticks each time its data
// is re-exported, so the pages re-fetch. On the public site there is no
// provider and every hook below returns its idle value.

const DesktopContext = createContext(null);

export const BUSY_STATES = new Set(['planning', 'running', 'exporting']);

export function DesktopProvider({ children }) {
  return IS_DESKTOP ? <LiveDesktop>{children}</LiveDesktop> : children;
}

function LiveDesktop({ children }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getStatus();
      setStatus(next);
      setError(null);
      return next;
    } catch (err) {
      setError(err);
      return null;
    }
  }, []);

  // Poll every second while anything runs, every five otherwise.
  useEffect(() => {
    let cancelled = false;
    let timer;
    const tick = async () => {
      const next = await refresh();
      if (!cancelled) timer = setTimeout(tick, next?.busy || !next?.ui?.ready ? 1000 : 5000);
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refresh]);

  useEffect(() => {
    ping();
    const id = setInterval(ping, 15000);
    window.addEventListener('pagehide', bye);
    return () => {
      clearInterval(id);
      window.removeEventListener('pagehide', bye);
    };
  }, []);

  const run = useCallback(
    async (options) => {
      await startRun(options);
      await refresh();
    },
    [refresh]
  );

  const value = useMemo(() => ({ status, error, run }), [status, error, run]);
  return <DesktopContext.Provider value={value}>{children}</DesktopContext.Provider>;
}

// { status, error, run } inside the app; null on the public site.
export function useDesktop() {
  return useContext(DesktopContext);
}

// One model's pass (see desktop/runner.py SportRun.snapshot), or null.
export function useModelRun(sportId) {
  const desktop = useDesktop();
  return desktop?.status?.models?.find((m) => m.id === sportId) ?? null;
}

// Ticks each time the app re-exports this sport's data (always 0 on the public site).
export function useDataVersion(sportId) {
  return useModelRun(sportId)?.dataVersion ?? 0;
}
