import { afterEach, expect, it, vi } from 'vitest';
import { findPublicDeliveryEvidence, sendBrowserRelay } from './relay.js';

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
  await sendBrowserRelay('https://relay.example', 'key', 1, 'hello');
  expect(JSON.parse(String(lastCall()[1].body))).toEqual({ ticket_id: 1, body: 'hello' });

  lastCall = stubRelay();
  await sendBrowserRelay('https://relay.example', 'key', 1, 'hello', { appendFooter: false });
  expect(JSON.parse(String(lastCall()[1].body))).toEqual({ ticket_id: 1, body: 'hello', append_footer: false });
});

it.each([
  { ok: true, status: 'sent', ticket_id: 19 },
  { ok: true, status: 'sent' },
  { ok: 'true', status: 'sent', ticket_id: 1 },
  { ok: true, status: 'queued', ticket_id: 1 },
])('rejects a relay response without a matching positive receipt %o', async response => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(response)));
  await expect(sendBrowserRelay('https://relay.example', 'key', 1, 'hello')).rejects.toThrow('did not confirm');
});

it('recognizes only fresh public staff history carrying the exact invisible marker', async () => {
  const lastCall = stubRelay();
  const id = '12345678-5678-4678-a678-123456789abc';
  const started = new Date('2026-09-01T00:00:00Z');
  await sendBrowserRelay('https://relay.example', 'key', 1, 'hello', { deliveryId: id });
  const [, init] = lastCall();
  const payload = JSON.parse(String(init.body));
  expect(init.redirect).toBe('error');
  expect(Object.keys(payload)).toEqual(['ticket_id', 'body']);
  expect(payload.body.replace(/[\u200b\u200c\u2063]/g, '')).toBe('hello');
  expect(payload.body).not.toContain(id);
  const event = { id: 'verified', created_at: '2026-09-01T00:00:01Z', public: true,
    author: { type: 'agent' }, content: { text: payload.body } };
  expect(findPublicDeliveryEvidence([event], id, started)).toEqual({ source: 'zendesk-conversation', message_id: 'verified' });
  expect(findPublicDeliveryEvidence([
    { ...event, author: { type: 'end-user', display_name: 'David Yang' } },
    { ...event, metadata: { is_public: false } },
    { ...event, public: undefined },
    { ...event, created_at: '2026-08-01T00:00:00Z' },
    { ...event, content: { text: 'hello' } },
  ], id, started)).toBeNull();
  expect(findPublicDeliveryEvidence([event], '12345678-5678-4678-a678-123456789abd', started)).toBeNull();
});
