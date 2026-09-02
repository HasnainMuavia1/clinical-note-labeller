import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Approval, FileDetail, JobDetail, api } from '../api/client';
import AgentRun from '../components/AgentRun';
import ApprovalCard from '../components/ApprovalCard';
import CodeEvidence from '../components/CodeEvidence';
import FileTree from '../components/FileTree';

export default function JobDetailPage() {
  const { jobId = '' } = useParams();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [files, setFiles] = useState<FileDetail[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [tree, setTree] = useState<string[]>([]);
  const [selected, setSelected] = useState<FileDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setJob(await api.getJob(jobId));
      setFiles((await api.listFiles(jobId)).items);
      setApprovals((await api.listApprovals(jobId)).items.filter((a) => a.status === 'pending'));
      setTree((await api.getTree(jobId)).paths);
    } catch (exc) {
      setError((exc as Error).message);
    }
  }, [jobId]);

  useEffect(() => {
    refresh();
    return api.subscribe(jobId, () => {
      refresh();
    });
  }, [jobId, refresh]);

  if (error) return <p className="error">{error}</p>;
  if (!job) return <p>Loading…</p>;

  return (
    <section className="job-record">
      <AgentRun jobId={jobId} showRecordLink={false} />
      {job.error && <p className="error">{job.error}</p>}

      {approvals.length > 0 && (
        <section className="approvals">
          <h3>Approvals needed ({approvals.length})</h3>
          {approvals.map((approval) => (
            <ApprovalCard key={approval.id} jobId={jobId} approval={approval} onDecided={refresh} />
          ))}
        </section>
      )}

      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Codes</th>
            <th>Specialty</th>
            <th>Method</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {files.map((file) => (
            <tr key={file.file_id} onClick={() => setSelected(file)}>
              <td>{file.filename}</td>
              <td>
                {file.status === 'unparsed'
                  ? 'unparsed'
                  : file.has_codes
                    ? 'with-codes'
                    : 'without-codes'}
              </td>
              <td>{file.specialty ?? '—'}</td>
              <td>{file.method ?? '—'}</td>
              <td>{file.confidence.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {selected && <CodeEvidence file={selected} />}
      <FileTree paths={tree} />
      <a className="download" href={api.downloadUrl(jobId)}>
        Download output.zip
      </a>
    </section>
  );
}
