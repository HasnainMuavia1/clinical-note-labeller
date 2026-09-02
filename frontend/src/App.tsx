import { NavLink, Route, Routes } from 'react-router-dom';
import { getApiKey, setApiKey } from './api/client';
import JobDetailPage from './pages/JobDetailPage';
import JobsPage from './pages/JobsPage';
import UploadPage from './pages/UploadPage';

export default function App() {
  return (
    <div className="app">
      <header>
        <h1>Clinical Note Labeller</h1>
        <nav>
          <NavLink to="/">Upload</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
        </nav>
        <input
          aria-label="API key"
          placeholder="API key"
          defaultValue={getApiKey()}
          onBlur={(event) => setApiKey(event.target.value)}
        />
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
