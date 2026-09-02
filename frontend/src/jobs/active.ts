import { JobSummary } from '../api/client';

const STORAGE_KEY = 'cnl.activeJobId';
const LIVE = new Set(['pending', 'running', 'awaiting_approval', 'awaiting_batch']);

export function rememberJobId(id: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* ignore */
  }
}

export function recalledJobId(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function pickActiveJob(jobs: JobSummary[], storedId: string | null): string | null {
  const live = jobs.find((job) => LIVE.has(job.status));
  if (live) return live.id;
  if (storedId && jobs.some((job) => job.id === storedId)) return storedId;
  return jobs[0]?.id ?? storedId;
}
