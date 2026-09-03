export interface RelaySendResult {
  ok: boolean;
  status: string;
  ticket_id: number;
  current_url?: string;
  error?: string;
}

export async function sendBrowserRelay(
  relayUrl: string,
  apiKey: string,
  ticketId: number,
  body: string
): Promise<RelaySendResult> {
  const normalizedUrl = relayUrl.replace(/\/+$/, '') + '/send';
  const res = await fetch(normalizedUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      ticket_id: ticketId,
      body: body,
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Browser relay failed with HTTP ${res.status}: ${text}`);
  }

  const data = (await res.json()) as any;
  if (!data.ok || data.status !== 'sent') {
    throw new Error(`Browser relay rejected message: ${JSON.stringify(data)}`);
  }

  return data as RelaySendResult;
}
