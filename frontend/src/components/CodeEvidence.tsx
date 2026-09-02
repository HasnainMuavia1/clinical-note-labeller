import { FileDetail } from '../api/client';

export default function CodeEvidence({ file }: { file: FileDetail }) {
  return (
    <section className="evidence">
      <h3>{file.filename}</h3>
      <p>
        Parser chain:{' '}
        {file.parse_trail.map((a) => `${a.parser}${a.ok ? '' : ' ✗'}`).join(' → ') || '—'}
      </p>
      <p>NPIs: {file.npis.join(', ') || 'none'}</p>
      <h4>Accepted codes</h4>
      <ul>
        {file.code_hits.map((hit, index) => (
          <li key={index}>
            <code>{hit.code}</code> [{hit.kind}] {hit.rule} &mdash; &ldquo;{hit.context}&rdquo;
          </li>
        ))}
      </ul>
      {file.code_hits.length === 0 && <p>None.</p>}
      <h4>Rejected candidates</h4>
      <ul>
        {file.code_rejected.map((hit, index) => (
          <li key={index}>
            <code>{hit.code}</code> {hit.rule}
          </li>
        ))}
      </ul>
      {file.code_rejected.length === 0 && <p>None.</p>}
    </section>
  );
}
