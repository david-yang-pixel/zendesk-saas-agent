import { describe, expect, it, vi } from 'vitest';
import { buildInternalDraftBody, deliverInternalDraft, formatInternalDraft, INTERNAL_DRAFT_MARKER } from './internal-draft-delivery.js';

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

  it('leads with the operator answer in the operator language, then the unsent English draft', async () => {
    const { options, fetchImpl } = fixture();
    await deliverInternalDraft({ ...options, operatorAnswer: '结论：该工作区在教育优惠期内，积分不按月刷新。\n- 优惠记录有效' });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).ticket.comment.body).toBe(
      '[DIFY_AI_AGENT_DRAFT:job-123]\nAI 调查结论（仅内部可见）\n\n结论：该工作区在教育优惠期内，积分不按月刷新。\n- 优惠记录有效'
      + '\n\n---\n客户回复草稿（英文，未发送给客户）：\n\nHello, we are checking your subscription.',
    );
  });

  it('labels an English operator answer in English and keeps the plain draft without one', () => {
    expect(formatInternalDraft('Hello!', 'The workspace is on the education promotion.'))
      .toBe('AI investigation answer (internal only)\n\nThe workspace is on the education promotion.\n\n---\nCustomer reply draft (not sent to the customer):\n\nHello!');
    expect(formatInternalDraft('Hello!', '  ')).toBe('AI-generated customer reply draft for human review. Not sent to the customer.\n\nHello!');
    expect(buildInternalDraftBody('job-1', 'Hello!', '结论：已核对。')).toMatch(/^\[DIFY_AI_AGENT_DRAFT:job-1\]\nAI 调查结论/);
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
