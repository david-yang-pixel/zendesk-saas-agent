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
}

export async function sendBrowserRelay(
  relayUrl: string,
  apiKey: string,
  ticketId: number,
  body: string,
  options: RelaySendOptions = {},
): Promise<RelaySendResult> {
  const normalizedUrl = relayUrl.replace(/\/+$/, '') + '/send';
  const start = Date.now();
  const res = await fetch(normalizedUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      ticket_id: ticketId,
      body: body,
      // Sent only to opt out, so ordinary replies keep the exact same payload.
      ...(options.appendFooter === false ? { append_footer: false } : {}),
    }),
    signal: options.signal,
  });

  const duration = Date.now() - start;

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Browser relay failed with HTTP ${res.status}: ${text}`);
  }

  const data = (await res.json()) as any;
  if (!data.ok || data.status !== 'sent') {
    throw new Error(`Browser relay rejected message: ${JSON.stringify(data)}`);
  }

  data.total_duration_ms = duration;
  return data as RelaySendResult;
}
