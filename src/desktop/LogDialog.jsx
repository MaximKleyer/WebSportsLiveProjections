import { useEffect, useRef, useState } from 'react';
import { getLog } from './api.js';

// A model's latest run log, following along (once a second) while it runs.
export default function LogDialog({ sportId, name, live, onClose }) {
  const dialog = useRef(null);
  const body = useRef(null);
  const [text, setText] = useState(null);

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer;
    const load = async () => {
      const next = await getLog(sportId);
      if (cancelled) return;
      const el = body.current;
      const following = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      setText(next);
      if (following) {
        requestAnimationFrame(() => {
          if (body.current) body.current.scrollTop = body.current.scrollHeight;
        });
      }
      if (live) timer = setTimeout(load, 1000);
    };
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sportId, live]);

  return (
    <dialog
      ref={dialog}
      className="log-dialog"
      aria-label={`${name} run log`}
      onClose={onClose}
      // A click on the backdrop (the dialog element itself) closes it.
      onClick={(e) => e.target === dialog.current && dialog.current.close()}
    >
      <div className="log-dialog__head">
        <span>
          {name} · Run log{live && <span className="log-dialog__live"> · live</span>}
        </span>
        <button type="button" className="desk-btn desk-btn--ghost" onClick={() => dialog.current?.close()}>
          Close
        </button>
      </div>
      <pre className="log-dialog__body" ref={body}>
        {text === null ? 'Loading…' : text || 'No log yet — this model hasn’t run from the app.'}
      </pre>
    </dialog>
  );
}
