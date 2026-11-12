import { afterEach, expect, it, vi } from 'vitest';
import { sendBrowserRelay } from './relay.js';

function stubRelay() {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, status: 'sent', ticket_id: 1 }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  return () => fetchMock.mock.calls[0] as unknown as [string, RequestInit];
}

afterEach(() => vi.unstubAllGlobals());

it('passes the caller abort signal to the relay request', async () => {
  const lastCall = stubRelay();
  const signal = AbortSignal.timeout(60_000);
  await sendBrowserRelay('https://relay.example/', 'key', 1, 'hello', { signal });
  const [url, init] = lastCall();
  expect(url).toBe('https://relay.example/send');
  expect(init.signal).toBe(signal);
});

it('keeps the ordinary payload unchanged and opts out of the footer only when asked', async () => {
  let lastCall = stubRelay();
  await sendBrowserRelay('https://relay.example', 'key', 7, 'hello');
  expect(JSON.parse(String(lastCall()[1].body))).toEqual({ ticket_id: 7, body: 'hello' });

  lastCall = stubRelay();
  await sendBrowserRelay('https://relay.example', 'key', 7, 'hello', { appendFooter: false });
  expect(JSON.parse(String(lastCall()[1].body))).toEqual({ ticket_id: 7, body: 'hello', append_footer: false });
});
