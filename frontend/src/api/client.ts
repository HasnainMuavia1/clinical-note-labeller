const BASE = (import.meta as any).env?.VITE_API_BASE ?? '/api/v1';

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface JobSummary {
  id: string;
  status: string;
  stage: string;
  progress: number;
  created_at: string;
  file_count: number;
}

export interface JobDetail extends JobSummary {
  original_filenames: string[];
  batch_id: string | null;
  error: string | null;
  pending_approvals: number;
}

export interface CodeHit {
  code: string;
  kind: string;
  rule: string;
  score: number;
  context: string;
  dictionary_hit: boolean;
}

export interface ParseAttempt {
  parser: string;
  ok: boolean;
  reason: string | null;
}

export interface FileDetail {
  file_id: string;
  filename: string;
  source_path: string;
  status: string;
  parser: string | null;
  parse_trail: ParseAttempt[];
  has_codes: boolean;
  code_hits: CodeHit[];
  code_rejected: CodeHit[];
  npis: string[];
  specialty: string | null;
  confidence: number;
  method: string | null;
  output_path: string | null;
}

export interface Approval {
  id: string;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Progress {
  status: string;
  stage: string;
  progress: number;
  pending_approvals: number;
}

export interface AuditEntry {
  id: string;
  action: string;
  detail: Record<string, unknown>;
  created_at: string;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (init.body && !(init.body instanceof FormData)) headers['Content-Type'] = 'application/json';

  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(problem.detail ?? problem.title ?? 'Request failed');
  }
  return (await response.json()) as T;
}

export const api = {
  createJob(files: File[]): Promise<JobDetail> {
    const form = new FormData();
    files.forEach((file) => form.append('files', file));
    return request<JobDetail>('/jobs', { method: 'POST', body: form });
  },
  listJobs(cursor?: string): Promise<Page<JobSummary>> {
    return request<Page<JobSummary>>(`/jobs${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
  },
  getJob(id: string): Promise<JobDetail> {
    return request<JobDetail>(`/jobs/${id}`);
  },
  listFiles(id: string): Promise<Page<FileDetail>> {
    return request<Page<FileDetail>>(`/jobs/${id}/files`);
  },
  listApprovals(id: string): Promise<Page<Approval>> {
    return request<Page<Approval>>(`/jobs/${id}/approvals`);
  },
  decideApproval(
    id: string,
    approvalId: string,
    body: {
      decision: 'approve' | 'reject';
      note?: string;
      specialty?: string;
      specialties?: Record<string, string>;
    },
  ): Promise<Approval> {
    return request<Approval>(`/jobs/${id}/approvals/${approvalId}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  getTree(id: string): Promise<{ root: string; paths: string[] }> {
    return request(`/jobs/${id}/tree`);
  },
  listAudit(id: string): Promise<Page<AuditEntry>> {
    return request(`/jobs/${id}/audit`);
  },
  listSpecialties(): Promise<{ items: { name: string; folder: string }[] }> {
    return request('/specialties');
  },
  downloadUrl(id: string): string {
    return `${BASE}/jobs/${id}/download`;
  },
  subscribe(id: string, onEvent: (event: Progress) => void): () => void {
    const source = new EventSource(`${BASE}/jobs/${id}/events`);
    source.addEventListener('progress', (event) =>
      onEvent(JSON.parse((event as MessageEvent).data)),
    );
    return () => source.close();
  },
};
