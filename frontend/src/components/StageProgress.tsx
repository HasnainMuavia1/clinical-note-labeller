const STAGES = [
  'intake',
  'unpack',
  'parse',
  'detect_codes',
  'resolve_npi',
  'classify',
  'plan_placement',
  'approval_gate',
  'execute_ops',
  'manifest',
];

export default function StageProgress({
  stage,
  status,
  progress,
}: {
  stage: string;
  status: string;
  progress: number;
}) {
  const current = STAGES.indexOf(stage);
  return (
    <div className="stages">
      <p>
        {status} &mdash; {Math.round(progress * 100)}%
      </p>
      <ol>
        {STAGES.map((name, index) => (
          <li key={name} className={index <= current ? 'done' : ''}>
            {name}
          </li>
        ))}
      </ol>
    </div>
  );
}
