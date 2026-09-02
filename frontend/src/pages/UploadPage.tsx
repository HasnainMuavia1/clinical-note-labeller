import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

export default function UploadPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const job = await api.createJob(files);
      navigate(`/jobs/${job.id}`);
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="dropzone"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        setFiles(Array.from(event.dataTransfer.files));
      }}
    >
      <p>Drop clinical notes here &mdash; PDF, DOCX, text, or ZIP.</p>
      <input type="file" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
      <ul>
        {files.map((file) => (
          <li key={file.name}>
            {file.name} ({Math.round(file.size / 1024)} KB)
          </li>
        ))}
      </ul>
      <button disabled={!files.length || busy} onClick={submit}>
        {busy ? 'Uploading…' : `Label ${files.length} file(s)`}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
