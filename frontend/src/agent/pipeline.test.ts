import { describe, expect, it } from 'vitest';
import {
  PIPELINE,
  buildTape,
  reasoningFor,
  stageIndex,
  stageProgress,
} from './pipeline';

describe('agent pipeline catalog', () => {
  it('lists every LangGraph node in run order', () => {
    expect(PIPELINE.map((step) => step.id)).toEqual([
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
    ]);
  });

  it('exposes a plan sentence for each agent', () => {
    for (const step of PIPELINE) {
      expect(step.plan.length).toBeGreaterThan(20);
      expect(step.agent).toMatch(/agent/i);
    }
  });
});

describe('reasoningFor', () => {
  it('explains intake from the uploaded filenames', () => {
    const text = reasoningFor('intake', {
      original_filenames: ['note.pdf', 'claim.zip'],
      status: 'running',
      file_count: 2,
    });
    expect(text.headline).toMatch(/intake/i);
    expect(text.detail).toContain('note.pdf');
    expect(text.detail).toContain('claim.zip');
  });

  it('summarizes code evidence after detection', () => {
    const text = reasoningFor('detect_codes', {
      status: 'running',
      files: [
        {
          filename: 'a.txt',
          has_codes: true,
          code_hits: [{ code: '99213' }, { code: 'E11.9' }],
          code_rejected: [{ code: '90210' }],
          parse_trail: [],
        },
      ],
    });
    expect(text.detail).toContain('99213');
    expect(text.detail).toMatch(/rejected/i);
  });

  it('shows how many notes have been parsed', () => {
    const text = reasoningFor('parse', {
      status: 'running',
      files_done: 47,
      files_total: 996,
      files: [],
    });
    expect(text.headline).toContain('47 of 996');
  });

  it('treats a completed run as the manifest seal even if stage is stale', () => {
    const text = reasoningFor('parse', { status: 'completed', files_done: 996, files_total: 996 });
    expect(text.headline).toMatch(/manifest|complete/i);
  });

  it('says when the run is waiting on a human', () => {
    const text = reasoningFor('approval_gate', {
      status: 'awaiting_approval',
      pending_approvals: 1,
    });
    expect(text.detail).toMatch(/approval|review/i);
  });
});

describe('buildTape', () => {
  it('turns audit steps into a chronological reasoning tape', () => {
    const tape = buildTape(
      { stage: 'parse', status: 'running', original_filenames: ['a.pdf'] },
      [],
      [
        { action: 'job_created', detail: { files: ['a.pdf'] }, created_at: '2026-09-02T10:00:00Z' },
        { action: 'agent_step', detail: { stage: 'intake', node: 'intake_node' }, created_at: '2026-09-02T10:00:01Z' },
        { action: 'agent_step', detail: { stage: 'parse', node: 'parse_node' }, created_at: '2026-09-02T10:00:02Z' },
      ],
    );
    expect(tape[0].kind).toBe('plan');
    expect(tape.some((entry) => entry.stage === 'intake')).toBe(true);
    expect(tape.at(-1)?.stage).toBe('parse');
  });

  it('keeps recent file_progress ticks on the tape', () => {
    const tape = buildTape(
      { stage: 'parse', status: 'running', files_done: 20, files_total: 100 },
      [],
      [
        { action: 'file_progress', detail: { stage: 'parse', done: 10, total: 100, filename: 'a.pdf' }, created_at: '2026-09-02T10:00:03Z' },
        { action: 'file_progress', detail: { stage: 'parse', done: 20, total: 100, filename: 'b.pdf' }, created_at: '2026-09-02T10:00:04Z' },
      ],
    );
    expect(tape.some((entry) => entry.headline.includes('20 of 100'))).toBe(true);
  });
});

describe('stage helpers', () => {
  it('ranks stages so later nodes score higher', () => {
    expect(stageIndex('manifest')).toBeGreaterThan(stageIndex('intake'));
    expect(stageProgress('manifest')).toBe(1);
    expect(stageProgress('parse')).toBeGreaterThan(stageProgress('intake'));
  });
});
