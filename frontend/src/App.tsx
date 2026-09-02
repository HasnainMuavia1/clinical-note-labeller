import { NavLink, Route, Routes } from 'react-router-dom';
import JobDetailPage from './pages/JobDetailPage';
import JobsPage from './pages/JobsPage';
import UploadPage from './pages/UploadPage';

export default function App() {
  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="brand__mark">CNL</span>
          <h1>Clinical Note Labeller</h1>
        </div>
        <nav>
          <NavLink to="/">Upload</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}
