import { expect, it, vi } from 'vitest';
import { sendBrowserRelay } from './relay.js';

it('passes the caller abort signal to the relay request', async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, status: 'sent', ticket_id: 1 }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  try {
    const signal = AbortSignal.timeout(60_000);
    await sendBrowserRelay('https://relay.example/', 'key', 1, 'hello', signal);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://relay.example/send');
    expect(init.signal).toBe(signal);
  } finally {
    vi.unstubAllGlobals();
  }
});
