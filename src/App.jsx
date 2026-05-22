import { Routes, Route } from 'react-router-dom';
import Header from './components/Header.jsx';
import Footer from './components/Footer.jsx';
import Landing from './pages/Landing.jsx';
import SportPage from './pages/SportPage.jsx';
import NotFound from './pages/NotFound.jsx';
import { SPORTS } from './config.js';

export default function App() {
  return (
    <div className="app-shell">
      <Header />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Landing />} />
          {SPORTS.map((sport) => (
            <Route
              key={sport.id}
              path={`/${sport.id}`}
              element={<SportPage sport={sport} />}
            />
          ))}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
