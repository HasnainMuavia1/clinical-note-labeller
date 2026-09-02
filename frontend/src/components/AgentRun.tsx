import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { PIPELINE, buildTape, reasoningFor, stageIndex } from '../agent/pipeline';
import { AuditEntry, FileDetail, JobDetail, api } from '../api/client';

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
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextJob, filePage, auditPage] = await Promise.all([
        api.getJob(jobId),
        api.listFiles(jobId),
        api.listAudit(jobId),
      ]);
      setJob(nextJob);
      setFiles(filePage.items);
      setAudit(auditPage.items);
      setError(null);
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
  if (!job) return <p className="muted">Connecting to the labelling graph…</p>;

  const current = stageIndex(job.stage);
  const live = reasoningFor(job.stage, { ...job, files });
  const tape = buildTape(job, files, audit);
  const pct = Math.round(job.progress * 100);

  return (
    <div className="agent-run">
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

      {showRecordLink && (
        <p className="agent-run__footer">
          <Link to={`/jobs/${jobId}`}>Open the full job record</Link>
        </p>
      )}
    </div>
  );
}
