import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './client';

afterEach(() => vi.restoreAllMocks());

describe('api client', () => {
  it('does not send an API key header', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await api.listJobs();

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers['X-API-Key']).toBeUndefined();
  });

  it('posts uploads as multipart form data', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ id: 'job-1' }), { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);

    const job = await api.createJob([new File(['x'], 'note.pdf')]);

    expect(job.id).toBe('job-1');
    expect(fetchMock.mock.calls[0][1].body).toBeInstanceOf(FormData);
  });

  it('subscribes to job events without an API key', () => {
    const instances: FakeEventSource[] = [];
    class FakeEventSource {
      url: string;
      listeners: Record<string, (event: MessageEvent) => void> = {};
      constructor(url: string) {
        this.url = url;
        instances.push(this);
      }
      addEventListener(type: string, handler: (event: MessageEvent) => void) {
        this.listeners[type] = handler;
      }
      close() {}
    }
    vi.stubGlobal('EventSource', FakeEventSource);

    const seen: unknown[] = [];
    const stop = api.subscribe('job-9', (event) => seen.push(event));
    expect(instances[0].url).toContain('/jobs/job-9/events');
    expect(instances[0].url).not.toContain('api_key=');
    instances[0].listeners.progress(
      new MessageEvent('progress', { data: JSON.stringify({ status: 'running', stage: 'parse', progress: 0.3 }) }),
    );
    expect(seen).toEqual([{ status: 'running', stage: 'parse', progress: 0.3 }]);
    stop();
  });

  it('raises the problem+json detail on error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ title: 'Not Found', detail: 'Job missing', status: 404 }), {
          status: 404,
          headers: { 'content-type': 'application/problem+json' },
        }),
      ),
    );

    await expect(api.getJob('nope')).rejects.toThrow('Job missing');
  });
});
