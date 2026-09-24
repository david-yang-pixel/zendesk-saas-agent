import { describe, expect, it, vi } from 'vitest';
import { buildInternalDraftBody, deliverInternalDraft, INTERNAL_DRAFT_MARKER } from './internal-draft-delivery.js';

function fixture() {
  let status: string | undefined;
  const query = vi.fn(async (sql: string) => {
    if (sql.startsWith('INSERT')) {
      if (status) return { rowCount: 0, rows: [] };
      status = 'attempting';
      return { rowCount: 1, rows: [{ job_id: 'job-123' }] };
    }
    if (sql.startsWith('SELECT')) return { rowCount: 1, rows: [{ ticket_id: '3726', status }] };
    if (sql.startsWith('UPDATE')) {
      status = 'sent';
      return { rowCount: 1, rows: [] };
    }
    throw new Error('Unexpected query');
  });
  const fetchImpl = vi.fn(async () => ({ ok: true, status: 200 } as Response));
  const options = {
    pool: { query } as any,
    jobId: 'job-123', ticketId: 3726,
    customerReply: 'Hello, we are checking your subscription.',
    baseUrl: 'https://example.zendesk.com', email: 'agent@example.com', apiToken: 'test-token',
    fetchImpl,
  };
  return { query, fetchImpl, options };
}

describe('internal customer-reply draft delivery', () => {
  it('posts only the sendable draft as a private comment without changing ticket fields', async () => {
    const { options, fetchImpl } = fixture();
    expect(await deliverInternalDraft(options)).toEqual({ channel: 'zendesk-internal-note', status: 'sent' });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://example.zendesk.com/api/v2/tickets/3726.json');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ ticket: {
      comment: {
        body: '[DIFY_AI_AGENT_DRAFT:job-123]\nAI-generated customer reply draft for human review. Not sent to the customer.\n\nHello, we are checking your subscription.',
        public: false,
      },
    } });
    expect(String(init.body)).toContain('Hello, we are checking your subscription.');
    expect(String(init.body)).not.toContain('Investigation summary');
    expect(String(init.body)).toContain(INTERNAL_DRAFT_MARKER);
  });

  it('does not resend an already delivered job', async () => {
    const { options, fetchImpl } = fixture();
    await deliverInternalDraft(options);
    expect(await deliverInternalDraft(options)).toEqual({ channel: 'zendesk-internal-note', status: 'already_sent' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('fails closed after an ambiguous HTTP result and never retries the write automatically', async () => {
    const { options, fetchImpl } = fixture();
    fetchImpl.mockRejectedValueOnce(new Error('connection reset'));
    await expect(deliverInternalDraft(options)).rejects.toThrow(/uncertain/);
    await expect(deliverInternalDraft(options)).rejects.toThrow(/uncertain/);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('rejects invalid destination and empty drafts before claiming a delivery fence', async () => {
    const { options, query } = fixture();
    await expect(deliverInternalDraft({ ...options, baseUrl: 'https://example.com' })).rejects.toThrow(/destination/);
    await expect(deliverInternalDraft({ ...options, customerReply: ' ' })).rejects.toThrow(/empty or too long/);
    expect(query).not.toHaveBeenCalled();
  });

  it('does not claim an internal draft was already sent to the customer', () => {
    const body = buildInternalDraftBody(
      'job-123',
      'Hello, we are checking this.\n\n---\nThis reply was sent by Dify AI Agent. Reply human if needed.',
    );
    expect(body).toContain('Hello, we are checking this.');
    expect(body).not.toContain('This reply was sent by Dify AI Agent');
  });

  it('keeps internal investigation material out of the customer-sendable note', async () => {
    const { options, fetchImpl } = fixture();
    const internalNote = 'Investigation summary (internal): private DB result and Authorization: Bearer abcdefghijklmnop';
    await deliverInternalDraft({ ...options, internalNote } as any);
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body)).ticket.comment.body as string;
    expect(body).toContain(options.customerReply);
    expect(body).not.toContain('Investigation summary');
    expect(body).not.toContain('private DB result');
    expect(body).not.toContain('abcdefghijklmnop');
  });
});
