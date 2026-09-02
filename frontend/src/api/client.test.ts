import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, setApiKey } from './client';

afterEach(() => vi.restoreAllMocks());

describe('api client', () => {
  it('sends the API key header', async () => {
    setApiKey('secret');
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await api.listJobs();

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers['X-API-Key']).toBe('secret');
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
