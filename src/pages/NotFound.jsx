import { Link } from 'react-router-dom';

export default function NotFound() {
  return (
    <div className="not-found">
      <span className="not-found__code">404</span>
      <h1 className="not-found__title">Off the board.</h1>
      <p className="not-found__body">That route doesn't exist yet.</p>
      <Link to="/" className="not-found__link">← Back to the desk</Link>
    </div>
  );
}
