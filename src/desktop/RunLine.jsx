import { useDesktop, useModelRun } from './DesktopProvider.jsx';
import { runHeadline } from './format.js';

// One line on what a sport's model is doing — on its landing card and its
// page. Renders nothing outside the desktop app.
export default function RunLine({ sportId, className = '', long = false }) {
  const desktop = useDesktop();
  const model = useModelRun(sportId);
  if (!desktop || !model) return null;

  const head = runHeadline(model, desktop.status?.now ?? Date.now());
  return (
    <p className={`run-line run-line--${head.tone} ${className}`.trim()}>
      <span className="run-line__dot" aria-hidden="true" />
      {head.text}
      {long && head.tone === 'busy' && ' · this page updates when it’s done'}
    </p>
  );
}
