import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, JobSummary } from '../api/client';

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listJobs()
      .then((page) => setJobs(page.items))
      .catch((exc) => setError((exc as Error).message));
  }, []);

  if (error) return <p className="error">{error}</p>;

  return (
    <section>
      <h2>Job log</h2>
      <table>
        <thead>
          <tr>
            <th>Job</th>
            <th>Status</th>
            <th>Stage</th>
            <th>Files</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                <Link to={`/jobs/${job.id}`}>{job.id.slice(0, 8)}</Link>
              </td>
              <td>{job.status}</td>
              <td>{job.stage}</td>
              <td>
                {job.files_done ?? 0}/{job.files_total || job.file_count}
              </td>
              <td>{new Date(job.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {jobs.length === 0 && <p>No jobs yet.</p>}
    </section>
  );
}
