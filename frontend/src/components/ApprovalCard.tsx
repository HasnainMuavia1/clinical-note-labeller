import { useEffect, useState } from 'react';
import { Approval, api } from '../api/client';

interface LowConfidenceFile {
  file_id: string;
  filename: string;
  proposed_specialty: string;
  confidence: number;
}

interface ProposedOp {
  op: string;
  target: string;
  reason: string;
}

export default function ApprovalCard({
  jobId,
  approval,
  onDecided,
}: {
  jobId: string;
  approval: Approval;
  onDecided: () => void;
}) {
  const [note, setNote] = useState('');
  const [specialties, setSpecialties] = useState<Record<string, string>>({});
  const [options, setOptions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isLowConfidence = approval.kind === 'low_confidence';
  const files = (approval.payload.files ?? []) as LowConfidenceFile[];
  const ops = (approval.payload.ops ?? []) as ProposedOp[];

  useEffect(() => {
    if (isLowConfidence) {
      api
        .listSpecialties()
        .then((r) => setOptions(r.items.map((i) => i.name)))
        .catch((exc) => setError((exc as Error).message));
    }
  }, [isLowConfidence]);

  async function decide(decision: 'approve' | 'reject') {
    setBusy(true);
    setError(null);
    try {
      await api.decideApproval(jobId, approval.id, {
        decision,
        note: note || undefined,
        specialties: isLowConfidence ? specialties : undefined,
      });
      onDecided();
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="approval">
      <h4>{approval.kind.replace(/_/g, ' ')}</h4>

      {isLowConfidence ? (
        <>
          <p>Pick a specialty for each file. Anything left blank is filed as Unclassified.</p>
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Model guess</th>
                <th>Confidence</th>
                <th>Specialty</th>
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <tr key={file.file_id}>
                  <td>{file.filename}</td>
                  <td>{file.proposed_specialty}</td>
                  <td>{file.confidence.toFixed(2)}</td>
                  <td>
                    <select
                      aria-label={`Specialty for ${file.filename}`}
                      value={specialties[file.file_id] ?? ''}
                      onChange={(event) =>
                        setSpecialties((prev) => ({ ...prev, [file.file_id]: event.target.value }))
                      }
                    >
                      <option value="">Unclassified</option>
                      {options.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <>
          <p>These operations would overwrite or delete existing files.</p>
          <ul>
            {ops.map((op) => (
              <li key={op.target}>
                <strong>{op.op}</strong> <code>{op.target}</code> &mdash; {op.reason}
              </li>
            ))}
          </ul>
          <p>Rejecting keeps the existing file and writes the new one with a numbered suffix.</p>
        </>
      )}

      <input placeholder="Note (optional)" value={note} onChange={(event) => setNote(event.target.value)} />
      <button disabled={busy} onClick={() => decide('approve')}>
        Approve
      </button>
      <button disabled={busy} onClick={() => decide('reject')}>
        Reject
      </button>
      {error && <p className="error">{error}</p>}
    </article>
  );
}
