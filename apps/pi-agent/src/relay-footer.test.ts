import { afterEach, expect, it, vi } from 'vitest';
import { publicDeliveryIdsInBody, sendBrowserRelay } from './relay.js';
import { customerFooter } from './reply-policy.js';

afterEach(() => vi.unstubAllGlobals());

it.each(['en', 'zh', 'ja', 'es'] as const)('preserves read-back evidence through Relay footer normalization for %s', async language => {
  const deliveryId = '8c4c3dd4-a9f5-4d25-8c63-833fac42c28c';
  const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => Response.json({ ok: true, status: 'sent', ticket_id: 42 }));
  vi.stubGlobal('fetch', fetchMock);
  const footer = customerFooter(language);
  await sendBrowserRelay('https://relay.invalid', 'test', 42, 'Respuesta.' + footer, { deliveryId });
  const init = fetchMock.mock.calls[0][1];
  const payload = JSON.parse(String(init.body));
  expect(new Headers(init.headers).get('Idempotency-Key')).toBe(deliveryId);
  expect(payload.body.endsWith(footer)).toBe(true);
  expect(publicDeliveryIdsInBody(payload.body.slice(0, -footer.length))).toEqual([deliveryId]);
  expect(payload.append_footer === false).toBe(language === 'es');
});
