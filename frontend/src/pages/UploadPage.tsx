import { useState } from 'react';
import { api } from '../api/client';
import { PIPELINE } from '../agent/pipeline';
import AgentRun from '../components/AgentRun';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  function takeFiles(list: FileList | File[]) {
    setFiles(Array.from(list));
    setError(null);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const job = await api.createJob(files);
      setJobId(job.id);
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workbench">
      <section className="intake">
        <div className="panel-head">
          <p className="eyebrow">Upload</p>
          <h2>Clinical notes</h2>
          <p className="lede">PDF, DOCX, text, or ZIP. Notes are copied into an output tree — originals are not changed.</p>
        </div>

        <div
          className={`dropwell${dragging ? ' is-dragging' : ''}${files.length ? ' has-files' : ''}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            takeFiles(event.dataTransfer.files);
          }}
        >
          {files.length === 0 ? (
            <div className="dropwell__empty">
              <p className="dropwell__hint">Drop files here</p>
              <label className="file-pick">
                <input
                  type="file"
                  multiple
                  accept=".pdf,.docx,.txt,.zip,text/plain,application/pdf,application/zip"
                  onChange={(event) => takeFiles(event.target.files ?? [])}
                />
                Choose files
              </label>
            </div>
          ) : (
            <>
              <div className="docket-head">
                <span>
                  {files.length} file{files.length === 1 ? '' : 's'} selected
                </span>
                <label className="file-pick file-pick--quiet">
                  <input
                    type="file"
                    multiple
                    accept=".pdf,.docx,.txt,.zip,text/plain,application/pdf,application/zip"
                    onChange={(event) => takeFiles(event.target.files ?? [])}
                  />
                  Replace
                </label>
              </div>
              <ul className="docket">
                {files.map((file) => (
                  <li key={`${file.name}-${file.size}-${file.lastModified}`}>
                    <span>{file.name}</span>
                    <span>{formatSize(file.size)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="panel-actions">
          <button className="primary" disabled={!files.length || busy} onClick={submit}>
            {busy ? 'Starting…' : `Run labelling (${files.length})`}
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      </section>

      <aside className="console">
        {jobId ? (
          <AgentRun jobId={jobId} />
        ) : (
          <>
            <div className="panel-head">
              <p className="eyebrow">Pipeline</p>
              <h2>What happens next</h2>
              <p className="lede">Ten steps, in order. Progress and reasoning appear here after you run a batch.</p>
            </div>
            <ol className="idle-plan">
              {PIPELINE.map((step, index) => (
                <li key={step.id}>
                  <span className="idle-plan__n">{String(index + 1).padStart(2, '0')}</span>
                  <div>
                    <strong>{step.title}</strong>
                    <span>{step.plan}</span>
                  </div>
                </li>
              ))}
            </ol>
          </>
        )}
      </aside>
    </div>
  );
}
