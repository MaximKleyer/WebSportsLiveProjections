import { BRAND } from '../config.js';

export default function Footer() {
  const year = new Date().getFullYear();
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <span className="site-footer__byline">{BRAND.byline}</span>
        <span className="site-footer__meta">© {year} · v0.1</span>
      </div>
    </footer>
  );
}
