import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PIPELINE, buildTape, reasoningFor, stageIndex } from '../agent/pipeline';
import { Approval, AuditEntry, FileDetail, JobDetail, api } from '../api/client';
import ApprovalCard from './ApprovalCard';

export default function AgentRun({
  jobId,
  showRecordLink = true,
}: {
  jobId: string;
  showRecordLink?: boolean;
}) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [files, setFiles] = useState<FileDetail[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextJob, filePage, auditPage, approvalPage] = await Promise.all([
        api.getJob(jobId),
        api.listFiles(jobId),
        api.listAudit(jobId),
        api.listApprovals(jobId),
      ]);
      setJob(nextJob);
      setFiles(filePage.items);
      setAudit(auditPage.items);
      setApprovals(approvalPage.items.filter((row) => row.status === 'pending'));
      setError(null);
    } catch (exc) {
      setError((exc as Error).message);
    }
  }, [jobId]);

  useEffect(() => {
    refresh();
    return api.subscribe(jobId, (event) => {
      setJob((prev) => (prev ? { ...prev, ...event } : prev));
      refresh();
    });
  }, [jobId, refresh]);

  if (error) return <p className="error">{error}</p>;
  if (!job) return <p className="muted">Connecting to the labelling graph…</p>;

  const stage = job.status === 'completed' ? 'manifest' : job.stage;
  const current = job.status === 'completed' ? PIPELINE.length : stageIndex(stage);
  const live = reasoningFor(stage, { ...job, files });
  const tape = buildTape({ ...job, stage }, files, audit);
  const pct = job.status === 'completed' ? 100 : Math.round(job.progress * 100);
  const filesDone = job.files_done ?? files.filter((file) => file.status !== 'pending').length;
  const filesTotal = job.files_total || job.file_count || files.length;
  const filePct = filesTotal ? Math.round((filesDone / filesTotal) * 100) : 0;

  const done = job.status === 'completed';

  return (
    <div className={`agent-run${done ? ' agent-run--complete' : ''}`}>
      <header className="agent-run__head">
        <div>
          <p className="eyebrow">Live graph</p>
          <h3>Labelling run {job.id.slice(0, 8)}</h3>
        </div>
        <div className="agent-run__meta">
          <span className={`stamp stamp--${job.status.replace(/_/g, '-')}`}>{job.status.replace(/_/g, ' ')}</span>
          <span className="pct">{pct}%</span>
        </div>
      </header>

      <p className="agent-run__live">
        <strong>{live.headline}</strong>
        <span>{live.detail}</span>
      </p>

      <div className="file-meter">
        <div className="file-meter__track">
          <div className="file-meter__bar" style={{ width: `${filePct}%` }} />
        </div>
        <p>
          {filesTotal
            ? `${filesDone} of ${filesTotal} files processed`
            : 'Waiting for the archive to unpack…'}
          {stage ? ` · ${stage.replace(/_/g, ' ')}` : ''}
        </p>
      </div>

      {approvals.length > 0 && (
        <section className="approvals">
          <h3>Approvals needed ({approvals.length})</h3>
          {approvals.map((approval) => (
            <ApprovalCard key={approval.id} jobId={jobId} approval={approval} onDecided={refresh} />
          ))}
        </section>
      )}

      <ol className="plan-rail" aria-label="Agent plan">
        {PIPELINE.map((step, index) => {
          const state = index < current ? 'done' : index === current ? 'active' : 'queued';
          return (
            <li key={step.id} className={state} title={step.plan}>
              <span className="plan-rail__agent">{step.agent.replace(' agent', '')}</span>
              <span className="plan-rail__title">{step.title}</span>
            </li>
          );
        })}
      </ol>

      <div className="tape" role="log" aria-live="polite">
        <p className="tape__label">Reasoning tape</p>
        {tape.map((entry, index) => (
          <article key={`${entry.headline}-${index}`} className={`ticket ticket--${entry.kind}`}>
            <header>
              <span>{entry.agent}</span>
              {entry.at && <time>{new Date(entry.at).toLocaleTimeString()}</time>}
            </header>
            <h4>{entry.headline}</h4>
            <p>{entry.detail}</p>
          </article>
        ))}
      </div>

      <p className="agent-run__footer">
        {done && (
          <a className="download" href={api.downloadUrl(jobId)}>
            Download output.zip
          </a>
        )}
        {showRecordLink && <Link to={`/jobs/${jobId}`}>Open the full job record</Link>}
      </p>
    </div>
  );
}
