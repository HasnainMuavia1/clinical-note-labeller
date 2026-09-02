import { describe, expect, it } from 'vitest';
import { pickActiveJob } from './active';
import { JobSummary } from '../api/client';

function job(partial: Partial<JobSummary> & { id: string; status: string }): JobSummary {
  return {
    stage: 'parse',
    progress: 0.2,
    created_at: '2026-09-02T00:00:00Z',
    file_count: 1,
    ...partial,
  };
}

describe('pickActiveJob', () => {
  it('prefers a running job over a stored completed one', () => {
    const chosen = pickActiveJob(
      [
        job({ id: 'done', status: 'completed' }),
        job({ id: 'live', status: 'running' }),
      ],
      'done',
    );
    expect(chosen).toBe('live');
  });

  it('falls back to the stored id', () => {
    expect(pickActiveJob([job({ id: 'done', status: 'completed' })], 'done')).toBe('done');
  });
});
