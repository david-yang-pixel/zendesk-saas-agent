export interface RelaySendResult {
  ok: boolean;
  status: string;
  ticket_id: number;
  current_url?: string;
  error?: string;
  timings?: Record<string, number>;
  total_duration_ms?: number;
}

export interface RelaySendOptions {
  signal?: AbortSignal;
  /**
   * The relay appends the "reply human" footer to every reply by default.
   * Pass false for a message where that prompt makes no sense, such as the
   * acknowledgement of a human request. A relay that predates this option
   * ignores it and still appends the footer.
   */
  appendFooter?: boolean;
  /** Locally generated identity carried in the body for Zendesk read-back. */
  deliveryId?: string;
}

export function publicDeliveryMarker(deliveryId: string): string {
  const hex = deliveryId.replace(/-/g, '');
  if (!/^[0-9a-f]{32}$/i.test(hex)) throw new Error('Invalid public delivery ID');
  // Encode an opaque UUID in zero-width characters. Browser Relay can use
  // its existing send contract. A channel that strips the marker gives no
  // recovery evidence, so an ambiguous send on that channel stays fenced.
  const bits = [...hex].map(digit => Number.parseInt(digit, 16).toString(2).padStart(4, '0')).join('');
  return '\u2063' + bits.replace(/0/g, '\u200b').replace(/1/g, '\u200c') + '\u2063';
}

export function publicDeliveryIdsInBody(body: string): string[] {
  return [...body.matchAll(/\u2063([\u200b\u200c]{128})\u2063/g)].map(([, bits]) => {
    const binary = bits.replace(/\u200b/g, '0').replace(/\u200c/g, '1');
    const hex = binary.match(/.{4}/g)!.map(nibble => Number.parseInt(nibble, 2).toString(16)).join('');
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  });
}

export function findPublicDeliveryEvidence(
  conversation: unknown[], deliveryId: string, startedAt: Date,
): { source: 'zendesk-conversation'; message_id: string } | null {
  const marker = publicDeliveryMarker(deliveryId);
  for (const value of conversation) {
    if (!value || typeof value !== 'object') continue;
    const event = value as Record<string, any>;
    const role = event.author?.type || event.author?.role;
    if (typeof role !== 'string' || !/^(?:agent|admin|staff|ai[-_]?agent)$/i.test(role) ||
        event.public === false || event.is_public === false || event.metadata?.is_public === false ||
        event.metadata?.public === false || event.content?.public === false) continue;
    const reference = typeof event.reference === 'string' ? event.reference : '';
    const isMessaging = /^zen:sunco:conversation_message:[^:]+$/.test(reference);
    if (!isMessaging && event.public !== true && event.is_public !== true &&
        event.metadata?.is_public !== true && event.metadata?.public !== true) continue;
    if (typeof event.created_at !== 'string' || !(Date.parse(event.created_at) >= startedAt.getTime() - 5_000)) continue;
    const id = typeof event.id === 'string' ? event.id : Number.isSafeInteger(event.id) ? String(event.id) : reference;
    if (!id.trim()) continue;
    const bodies = [event.content, event.content?.text, event.content?.text_fallback, event.content?.body,
      event.body, event.body?.text, event.message, event.message?.text];
    if (bodies.some(body => typeof body === 'string' && body.includes(marker))) {
      return { source: 'zendesk-conversation', message_id: id };
    }
  }
  return null;
}

export async function sendBrowserRelay(
  relayUrl: string,
  apiKey: string,
  ticketId: number,
  body: string,
  options: RelaySendOptions = {},
): Promise<RelaySendResult> {
  const normalizedUrl = relayUrl.replace(/\/+$/, '') + '/send';
  // The Seoul Relay strips a recognized footer suffix before adding its own.
  // Store the marker before that boundary so the customer-visible body can be reconciled.
  const footer = body.search(/\n\n---\n(?=This reply was sent by Dify AI Agent\.|此回复由 Dify AI Agent 发送。|この返信は Dify AI Agent が送信しました。|Dify AI Agent envió esta respuesta\.)/u);
  const split = footer < 0 ? body.length : footer;
  const payloadBody = options.deliveryId ? body.slice(0, split) + publicDeliveryMarker(options.deliveryId) + body.slice(split) : body;
  const spanishFooter = footer >= 0 && body.slice(footer).startsWith('\n\n---\nDify AI Agent envió esta respuesta.');
  const start = Date.now();
  const res = await fetch(normalizedUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
      ...(options.deliveryId ? { 'Idempotency-Key': options.deliveryId } : {}),
    },
    body: JSON.stringify({
      ticket_id: ticketId,
      body: payloadBody,
      ...(options.appendFooter === false || spanishFooter ? { append_footer: false } : {}),
    }),
    signal: options.signal,
    // Following a redirect may replay the POST against a second destination.
    redirect: 'error',
  });

  const duration = Date.now() - start;

  if (!res.ok) {
    throw new Error(`Browser relay failed with HTTP ${res.status}`);
  }
  const data = await res.json() as (Omit<RelaySendResult, 'ticket_id'> & { ticket_id: unknown }) | null;
  if (data?.ok !== true || data.status !== 'sent' ||
      (data.ticket_id !== ticketId && data.ticket_id !== String(ticketId))) {
    throw new Error('Browser relay did not confirm this ticket was sent');
  }

  return { ...data, ticket_id: ticketId, total_duration_ms: duration };
}
