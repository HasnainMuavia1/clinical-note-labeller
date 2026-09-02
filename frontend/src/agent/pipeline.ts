export interface PipelineStep {
  id: string;
  agent: string;
  title: string;
  plan: string;
}

export interface Reasoning {
  headline: string;
  detail: string;
}

export interface TapeEntry {
  kind: 'plan' | 'step' | 'decision';
  stage?: string;
  agent: string;
  headline: string;
  detail: string;
  at?: string;
}

export const PIPELINE: PipelineStep[] = [
  {
    id: 'intake',
    agent: 'Intake agent',
    title: 'Intake',
    plan: 'Inventory uploaded bytes, hash each note, and leave the originals untouched in input/.',
  },
  {
    id: 'unpack',
    agent: 'Archive agent',
    title: 'Unpack',
    plan: 'Expand ZIP bundles into extracted/ and reject path-escaping archive members.',
  },
  {
    id: 'parse',
    agent: 'Parse agent',
    title: 'Parse',
    plan: 'Extract text with pypdf / python-docx, then LlamaParse, then sandbox OCR. Failures go to unparsed/.',
  },
  {
    id: 'detect_codes',
    agent: 'Coding agent',
    title: 'Detect codes',
    plan: 'Find ICD-10, CPT, HCPCS, modifiers, and NPIs, then score each candidate against dictionaries and nearby cues.',
  },
  {
    id: 'resolve_npi',
    agent: 'NPI agent',
    title: 'Resolve NPI',
    plan: 'Look up validated NPIs on NPPES and map taxonomy codes to a specialty when the provider is an individual.',
  },
  {
    id: 'classify',
    agent: 'Specialty agent',
    title: 'Classify',
    plan: 'Ask the configured mini model to label notes that still have no NPI specialty. Low-confidence labels wait for review.',
  },
  {
    id: 'plan_placement',
    agent: 'Planning agent',
    title: 'Plan placement',
    plan: 'Propose a copy into with-codes/ or without-codes/ under the specialty folder, with collision handling.',
  },
  {
    id: 'approval_gate',
    agent: 'Guard agent',
    title: 'Approval gate',
    plan: 'Park the run if an overwrite, delete, or low-confidence specialty needs a human decision.',
  },
  {
    id: 'execute_ops',
    agent: 'File agent',
    title: 'Execute',
    plan: 'Copy labelled notes into the output tree. Uploaded files are never modified or deleted.',
  },
  {
    id: 'manifest',
    agent: 'Manifest agent',
    title: 'Manifest',
    plan: 'Write manifest.jsonl and labels.csv with every accepted and rejected code plus the parse trail.',
  },
];

const PROGRESS: Record<string, number> = {
  intake: 0.08,
  unpack: 0.16,
  parse: 0.32,
  detect_codes: 0.48,
  resolve_npi: 0.58,
  classify: 0.72,
  plan_placement: 0.82,
  approval_gate: 0.88,
  execute_ops: 0.94,
  manifest: 1,
};

export function stageIndex(stage: string): number {
  return PIPELINE.findIndex((step) => step.id === stage);
}

export function stageProgress(stage: string): number {
  return PROGRESS[stage] ?? 0;
}

export function stepById(stage: string): PipelineStep | undefined {
  return PIPELINE.find((step) => step.id === stage);
}

type JobLike = {
  status?: string;
  stage?: string;
  file_count?: number;
  files_done?: number;
  files_total?: number;
  original_filenames?: string[];
  pending_approvals?: number;
  error?: string | null;
  files?: FileLike[];
};

type FileLike = {
  filename?: string;
  status?: string;
  has_codes?: boolean;
  code_hits?: { code?: string }[];
  code_rejected?: { code?: string }[];
  parse_trail?: { parser?: string; ok?: boolean; reason?: string | null }[];
  specialty?: string | null;
  method?: string | null;
  confidence?: number;
  npis?: string[];
};

export type AuditLike = {
  action: string;
  detail: Record<string, unknown>;
  created_at?: string;
};

function namesOf(job: JobLike): string[] {
  if (job.original_filenames?.length) return job.original_filenames;
  if (job.files?.length) return job.files.map((file) => file.filename ?? 'note');
  return [];
}

export function reasoningFor(stage: string, job: JobLike = {}): Reasoning {
  const step = stepById(stage);
  const names = namesOf(job);
  const files = job.files ?? [];
  const fileLabel = names.length ? names.join(', ') : `${job.file_count ?? 0} file(s)`;
  const doneCount = job.files_done ?? files.filter((file) => file.status && file.status !== 'pending').length;
  const totalCount = job.files_total || job.file_count || files.length || names.length;
  const countLabel = totalCount ? `${doneCount} of ${totalCount} notes` : fileLabel;

  if (job.status === 'failed' && job.error) {
    return { headline: 'Run failed', detail: job.error };
  }
  if (job.status === 'completed') {
    return {
      headline: `Manifest — ${countLabel} filed`,
      detail: 'The labelling graph finished. Notes are in the output tree with specialties, codes, and the manifest.',
    };
  }

  switch (stage) {
    case 'intake':
      return {
        headline: 'Intake — receiving the encounter batch',
        detail: `Hashing and inventorying ${fileLabel}. Originals stay in input/; nothing is rewritten in place.`,
      };
    case 'unpack':
      return {
        headline: 'Unpack — expanding archives',
        detail: `Opening ZIP members for ${fileLabel}. Path-escaping entries are refused and audited.`,
      };
    case 'parse': {
      const latest = [...files].reverse().find((file) => (file.parse_trail ?? []).length);
      const trails = latest?.parse_trail ?? [];
      const lastFail = trails.find((attempt) => !attempt.ok);
      const chain = trails.map((attempt) => attempt.parser).filter(Boolean).join(' → ') || 'pypdf → LlamaParse → OCR';
      return {
        headline: `Parse — ${countLabel}`,
        detail: lastFail?.reason
          ? `${chain} on ${latest?.filename ?? 'the latest note'}. Last miss: ${lastFail.reason}.`
          : `Running ${chain}. Finished ${countLabel}.`,
      };
    }
    case 'detect_codes': {
      const hits = files.flatMap((file) => file.code_hits ?? []);
      const rejected = files.flatMap((file) => file.code_rejected ?? []);
      const hitList = hits.map((hit) => hit.code).filter(Boolean).slice(0, 6).join(', ') || 'none yet';
      return {
        headline: `Coding — ${countLabel}`,
        detail: `Accepted ${hits.length} (${hitList}). Rejected ${rejected.length} lookalikes (ZIP, dates, vitals) that matched a code shape without evidence.`,
      };
    }
    case 'resolve_npi': {
      const npis = files.flatMap((file) => file.npis ?? []);
      return {
        headline: `NPI — ${countLabel}`,
        detail: npis.length
          ? `Looking up ${npis.join(', ')} on NPPES and mapping taxonomy to a specialty.`
          : 'No valid NPIs in this batch; specialty will come from the classifier.',
      };
    }
    case 'classify': {
      const labelled = files.filter((file) => file.specialty);
      const method = labelled[0]?.method ?? 'model';
      return {
        headline: 'Classify — assigning specialty',
        detail: labelled.length
          ? `${labelled.length} note(s) labelled (${method}). Remaining notes go to the mini model if they still have no specialty.`
          : 'Sending notes without an NPI specialty to the configured mini model.',
      };
    }
    case 'plan_placement':
      return {
        headline: 'Plan — proposing the output tree',
        detail: 'Each note is planned as a copy into with-codes/<Specialty>/ or without-codes/<Specialty>/. Collisions become overwrite approvals.',
      };
    case 'approval_gate':
      return {
        headline: job.status === 'awaiting_approval' ? 'Paused — human review required' : 'Guard — checking destructive ops',
        detail:
          job.status === 'awaiting_approval' || (job.pending_approvals ?? 0) > 0
            ? 'The graph is parked for human approval. Review the pending decision to resume.'
            : 'No overwrite or low-confidence gate fired; continuing to execute.',
      };
    case 'execute_ops':
      return {
        headline: 'Execute — filing labelled notes',
        detail: 'Copying planned targets into output/. Uploaded bytes in input/ are not modified.',
      };
    case 'manifest':
      return {
        headline: 'Manifest — sealing the run',
        detail: 'Writing manifest.jsonl and labels.csv with parse trail, accepted codes, rejected candidates, and destination path.',
      };
    default:
      return {
        headline: step ? `${step.title} — ${step.agent}` : 'Waiting for the first agent',
        detail: step?.plan ?? 'The labelling graph has not started yet.',
      };
  }
}

export function buildTape(job: JobLike, files: FileLike[], audits: AuditLike[]): TapeEntry[] {
  const combined: JobLike = { ...job, files, original_filenames: job.original_filenames ?? namesOf(job) };
  const recentFileTicks = new Set(audits.filter((entry) => entry.action === 'file_progress').slice(-8));
  const tape: TapeEntry[] = [
    {
      kind: 'plan',
      agent: 'Supervisor',
      headline: 'Plan — ten-agent labelling graph',
      detail: PIPELINE.map((step) => `${step.title}: ${step.plan}`).join(' '),
    },
  ];

  for (const entry of audits) {
    if (entry.action === 'job_created') {
      const created = (entry.detail.files as string[] | undefined)?.join(', ') ?? namesOf(combined).join(', ');
      tape.push({
        kind: 'step',
        stage: 'intake',
        agent: 'Intake agent',
        headline: 'Job accepted',
        detail: created ? `Queued ${created}.` : 'Queued the upload.',
        at: entry.created_at,
      });
      continue;
    }
    if (entry.action === 'agent_step') {
      const stage = String(entry.detail.stage ?? '');
      const step = stepById(stage);
      const reasoned = reasoningFor(stage, combined);
      tape.push({
        kind: 'step',
        stage,
        agent: step?.agent ?? String(entry.detail.node ?? 'agent'),
        headline: reasoned.headline,
        detail: reasoned.detail,
        at: entry.created_at,
      });
      continue;
    }
    if (entry.action === 'file_progress') {
      if (!recentFileTicks.has(entry)) continue;
      const stage = String(entry.detail.stage ?? '');
      const done = Number(entry.detail.done ?? 0);
      const total = Number(entry.detail.total ?? 0);
      const filename = String(entry.detail.filename ?? 'note');
      tape.push({
        kind: 'step',
        stage,
        agent: stepById(stage)?.agent ?? 'Agent',
        headline: `${stepById(stage)?.title ?? stage} — ${done} of ${total}`,
        detail: `Finished ${filename}.`,
        at: entry.created_at,
      });
      continue;
    }
    if (entry.action === 'approval_requested') {
      tape.push({
        kind: 'decision',
        stage: 'approval_gate',
        agent: 'Guard agent',
        headline: 'Human approval requested',
        detail: `Kind: ${String(entry.detail.kind ?? 'review')}. The graph is parked until you decide.`,
        at: entry.created_at,
      });
    }
  }

  if (tape.length === 1 && combined.stage) {
    const reasoned = reasoningFor(combined.stage, combined);
    tape.push({
      kind: 'step',
      stage: combined.stage,
      agent: stepById(combined.stage)?.agent ?? 'Agent',
      headline: reasoned.headline,
      detail: reasoned.detail,
    });
  }

  return tape;
}
