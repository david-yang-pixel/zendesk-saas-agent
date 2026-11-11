import { describe, expect, it, vi } from 'vitest';
import {
  addZendeskTicketTags,
  handoffAcknowledgement,
  handoffLanguage,
  isWithinSupportHours,
  MAX_HANDOFF_ATTEMPTS,
  processHandoff,
  recordWorkerHandoff,
  SUPPORT_HOURS_TEXT,
  type HandoffDependencies,
  type HandoffRow,
} from './human-handoff.js';
import { customerFooter } from './reply-policy.js';

describe('isWithinSupportHours', () => {
  it.each([
    ['2026-11-09T00:59:59Z', false], // Sunday 7:59 PM EST
    ['2026-11-09T01:00:00Z', true], // Sunday 8:00 PM EST, shift starts
    ['2026-11-11T09:59:59Z', true], // Wednesday night shift
    ['2026-11-11T10:00:00Z', false], // 5:00 AM EST, shift ends
    ['2026-11-13T09:30:00Z', true], // Thursday night shift, Friday UTC
    ['2026-11-13T10:00:00Z', false],
    ['2026-11-14T03:00:00Z', false], // Friday night New York: no shift
    ['2026-11-15T03:00:00Z', false], // Saturday night New York: no shift
    ['2026-07-06T01:00:00Z', true], // Sunday 9:00 PM EDT
    ['2026-07-06T00:30:00Z', false], // Sunday 8:30 PM EDT, before the summer shift
    ['2026-07-10T09:59:00Z', true], // Friday 5:59 AM EDT
  ])('%s -> %s', (iso, expected) => {
    expect(isWithinSupportHours(new Date(iso))).toBe(expected);
  });
});

describe('acknowledgement text', () => {
  it('promises a prompt human reply inside support hours', () => {
    expect(handoffAcknowledgement('zh', true)).toBe('您好，已为您转接人工支持，工程师马上就来，请您稍等一会。');
    expect(handoffAcknowledgement('en', true)).toContain('will be with you shortly');
    expect(handoffAcknowledgement('ja', true)).toContain('少々お待ちください');
  });

  it('names the support hours outside them', () => {
    for (const language of ['en', 'zh', 'ja'] as const) {
      expect(handoffAcknowledgement(language, false)).toContain(SUPPORT_HOURS_TEXT[language]);
    }
    expect(handoffAcknowledgement('zh', false)).toContain('人工支持会在服务时间内第一时间查看您的问题');
    expect(handoffAcknowledgement('en', false)).toContain('first thing during support hours');
  });

  it('uses the same support hours as the reply footer', () => {
    for (const language of ['en', 'zh', 'ja'] as const) {
      expect(customerFooter(language)).toContain(SUPPORT_HOURS_TEXT[language]);
    }
  });
});

describe('handoffLanguage', () => {
  it('ignores the English word human and uses earlier customer turns', () => {
    const context = 'Customer [t1]:\n我的应用无法发布\n\nSupport [t2]:\n请检查配置\n\nCustomer [t3]:\nhuman';
    expect(handoffLanguage('human', context)).toBe('zh');
    expect(handoffLanguage('Human!', 'Customer [t1]:\nアプリが公開できません\n\nCustomer [t2]:\nhuman')).toBe('ja');
  });

  it('reads 人工 as Chinese and falls back to English', () => {
    expect(handoffLanguage('人工')).toBe('zh');
    expect(handoffLanguage('human')).toBe('en');
  });
});

describe('addZendeskTicketTags', () => {
  it('adds tags with PUT on the tags endpoint', async () => {
    const fetchImpl = vi.fn(async () => new Response('{}', { status: 200 }));
    await addZendeskTicketTags({
      baseUrl: 'https://example.zendesk.com', email: 'agent@example.com', apiToken: 'token',
      ticketId: 42, tags: ['human_requested'], fetchImpl,
    });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://example.zendesk.com/api/v2/tickets/42/tags.json');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ tags: ['human_requested'] });
  });

  it('rejects a failed response without echoing its body', async () => {
    const fetchImpl = vi.fn(async () => new Response('secret customer data', { status: 403 }));
    await expect(addZendeskTicketTags({
      baseUrl: 'https://example.zendesk.com', email: 'a@example.com', apiToken: 't',
      ticketId: 42, tags: ['x'], fetchImpl,
    })).rejects.toThrow(/^Zendesk tag request failed with HTTP 403$/);
  });
});

function row(overrides: Partial<HandoffRow> = {}): HandoffRow {
  return {
    handoff_id: 'evt-human', ticket_id: 5459, acknowledge: true, customer_message: 'human',
    attempts: 1, alerted_at: null, tagged_at: null, ack_state: null, ...overrides,
  };
}

function deps(overrides: Partial<HandoffDependencies> = {}) {
  const queries: Array<[string, unknown[]]> = [];
  const pool = {
    query: vi.fn(async (sql: string, params: unknown[] = []) => {
      queries.push([sql.replace(/\s+/g, ' ').trim(), params]);
      return { rows: [], rowCount: /ack_state = 'attempting'/.test(sql) ? 1 : 0 };
    }),
  };
  const value: HandoffDependencies = {
    pool: pool as unknown as HandoffDependencies['pool'],
    tag: 'human_requested',
    now: () => new Date('2026-11-11T03:00:00Z'), // Tuesday night shift
    alertTracker: { shouldAlert: vi.fn(() => true), recordAlert: vi.fn() },
    sendAlert: vi.fn(async () => true),
    addTags: vi.fn(async () => {}),
    loadConversation: vi.fn(async () => ({ message: 'human', conversationContext: 'Customer [t1]:\n工作流报错了\n\nCustomer [t2]:\nhuman' })),
    sendReply: vi.fn(async () => {}),
    log: { log: vi.fn(), warn: vi.fn() },
    ...overrides,
  };
  return { value, queries };
}

const statusOf = (queries: Array<[string, unknown[]]>) =>
  queries.map(([sql]) => sql.match(/SET (status = '\w+'|ack_state = '\w+'|tagged_at|alerted_at)/)?.[1]).filter(Boolean);

describe('processHandoff', () => {
  it('tags, alerts and acknowledges in the customer language inside support hours', async () => {
    const { value, queries } = deps();
    await processHandoff(row(), value);
    expect(value.addTags).toHaveBeenCalledWith(5459, ['human_requested']);
    expect(value.sendAlert).toHaveBeenCalledOnce();
    expect(vi.mocked(value.sendAlert).mock.calls[0][0]).toMatchObject({
      ticketId: 5459,
      status: expect.stringContaining('支持时间内'),
      note: expect.stringContaining('已给工单打标签 human_requested'),
    });
    expect(value.sendReply).toHaveBeenCalledWith(5459, '您好，已为您转接人工支持，工程师马上就来，请您稍等一会。');
    expect(statusOf(queries)).toEqual(['tagged_at', 'alerted_at', "ack_state = 'attempting'", "ack_state = 'sent'", "status = 'completed'"]);
  });

  it('tells the customer about support hours outside them', async () => {
    const { value } = deps({
      now: () => new Date('2026-11-14T15:00:00Z'), // Saturday
      loadConversation: vi.fn(async () => ({ message: 'human', conversationContext: 'Customer [t1]:\nMy app fails\n\nCustomer [t2]:\nhuman' })),
    });
    await processHandoff(row(), value);
    expect(vi.mocked(value.sendReply).mock.calls[0][1]).toContain('currently outside support hours');
    expect(vi.mocked(value.sendAlert).mock.calls[0][0].status).toContain('不在支持时间内');
  });

  it('does not answer a repeated command and respects the alert cooldown', async () => {
    const { value } = deps({ alertTracker: { shouldAlert: vi.fn(() => false), recordAlert: vi.fn() } });
    await processHandoff(row({ acknowledge: false }), value);
    expect(value.sendReply).not.toHaveBeenCalled();
    expect(value.loadConversation).not.toHaveBeenCalled();
    expect(value.sendAlert).not.toHaveBeenCalled();
    expect(value.addTags).toHaveBeenCalledOnce();
  });

  it('always announces the first command of a takeover, even inside the cooldown', async () => {
    const { value } = deps({ alertTracker: { shouldAlert: vi.fn(() => false), recordAlert: vi.fn() } });
    await processHandoff(row(), value);
    expect(value.sendAlert).toHaveBeenCalledOnce();
  });

  it('alerts with the draft and never retries when the acknowledgement fails', async () => {
    const { value, queries } = deps({ sendReply: vi.fn(async () => { throw new Error('relay down'); }) });
    await processHandoff(row(), value);
    expect(vi.mocked(value.sendAlert).mock.calls[1][0]).toMatchObject({
      title: '🚨 转人工确认消息发送失败', reason: 'relay down', draftReply: expect.stringContaining('转接人工支持'),
    });
    expect(statusOf(queries)).toContain("ack_state = 'failed'");
    expect(statusOf(queries).at(-1)).toBe("status = 'completed'");
  });

  it('does not resend an acknowledgement that was interrupted mid-send', async () => {
    const { value, queries } = deps();
    await processHandoff(row({ alerted_at: new Date(), tagged_at: new Date(), ack_state: 'attempting', attempts: 2 }), value);
    expect(value.sendReply).not.toHaveBeenCalled();
    expect(statusOf(queries)).toEqual(["ack_state = 'uncertain'", "status = 'completed'"]);
    expect(vi.mocked(value.sendAlert).mock.calls[0][0].title).toBe('⚠️ 转人工确认消息状态未知');
  });

  it('retries only the tag, then gives up with a low-priority alert', async () => {
    const failingTag = vi.fn(async () => { throw new Error('Zendesk tag request failed with HTTP 500'); });
    const first = deps({ addTags: failingTag });
    await processHandoff(row(), first.value);
    expect(first.value.sendReply).toHaveBeenCalledOnce();
    expect(vi.mocked(first.value.sendAlert).mock.calls[0][0].note).toContain('打标签 human_requested 失败');
    expect(statusOf(first.queries).at(-1)).toBe("status = 'pending'");

    const last = deps({ addTags: failingTag });
    await processHandoff(row({ attempts: MAX_HANDOFF_ATTEMPTS, alerted_at: new Date(), ack_state: 'sent' }), last.value);
    expect(last.value.sendReply).not.toHaveBeenCalled();
    expect(statusOf(last.queries).at(-1)).toBe("status = 'failed'");
    expect(vi.mocked(last.value.sendAlert).mock.calls[0][0].title).toContain('打标签失败');
  });

  it('retries an alert Feishu did not accept, without resending the reply', async () => {
    const tracker = { shouldAlert: vi.fn(() => true), recordAlert: vi.fn() };
    const first = deps({ alertTracker: tracker, sendAlert: vi.fn(async () => false) });
    await processHandoff(row(), first.value);
    expect(tracker.recordAlert).not.toHaveBeenCalled();
    expect(first.value.sendReply).toHaveBeenCalledOnce();
    expect(statusOf(first.queries)).toEqual(['tagged_at', "ack_state = 'attempting'", "ack_state = 'sent'", "status = 'pending'"]);
    expect(first.queries.at(-1)?.[1]).toContain('Feishu alert was not delivered');

    const retry = deps({ alertTracker: tracker });
    await processHandoff(row({ attempts: 2, tagged_at: new Date(), ack_state: 'sent' }), retry.value);
    expect(retry.value.sendReply).not.toHaveBeenCalled();
    expect(vi.mocked(retry.value.sendAlert).mock.calls[0][0].note).toContain('已自动回复客户');
    expect(tracker.recordAlert).toHaveBeenCalledWith(5459);
    expect(statusOf(retry.queries)).toEqual(['alerted_at', "status = 'completed'"]);
  });

  it('re-sends the takeover alert once more when the alert keeps failing', async () => {
    const sendAlert = vi.fn(async () => false);
    const { value, queries } = deps({ sendAlert });
    await processHandoff(row({ attempts: MAX_HANDOFF_ATTEMPTS, tagged_at: new Date(), ack_state: 'sent' }), value);
    expect(statusOf(queries).at(-1)).toBe("status = 'failed'");
    expect(sendAlert.mock.calls.at(-1)?.[0].title).toContain('多次发送失败后重发');
  });

  it('re-opens the takeover alert when the reply-failure alert is not delivered', async () => {
    const sendAlert = vi.fn(async (params: { title: string }) => !params.title.includes('发送失败'));
    const first = deps({ sendAlert, sendReply: vi.fn(async () => { throw new Error('relay down'); }) });
    await processHandoff(row(), first.value);
    const firstSql = first.queries.map(([sql]) => sql);
    expect(firstSql.some(sql => sql.startsWith('UPDATE human_handoffs SET alerted_at = NULL'))).toBe(true);
    expect(statusOf(first.queries).at(-1)).toBe("status = 'pending'");
    expect(String(first.queries.at(-1)?.[1][1])).toContain('reply-failure alert was not delivered');

    const retry = deps();
    await processHandoff(row({ attempts: 2, tagged_at: new Date(), ack_state: 'failed' }), retry.value);
    expect(retry.value.sendReply).not.toHaveBeenCalled();
    expect(vi.mocked(retry.value.sendAlert).mock.calls[0][0]).toMatchObject({
      note: expect.stringContaining('自动确认消息发送失败或状态未知'),
      draftReply: expect.stringContaining('Thanks for reaching out'),
    });
    expect(statusOf(retry.queries)).toEqual(['alerted_at', "status = 'completed'"]);
  });

  it('reports a timed-out reply as uncertain instead of failed', async () => {
    const timeout = Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' });
    const { value, queries } = deps({ sendReply: vi.fn(async () => { throw timeout; }) });
    await processHandoff(row(), value);
    expect(statusOf(queries)).toContain("ack_state = 'uncertain'");
    expect(statusOf(queries)).not.toContain("ack_state = 'failed'");
    expect(vi.mocked(value.sendAlert).mock.calls[1][0]).toMatchObject({
      title: '⚠️ 转人工确认消息状态未知', draftReply: expect.stringContaining('转接人工支持'),
    });
    expect(statusOf(queries).at(-1)).toBe("status = 'completed'");
  });

  it('skips the acknowledgement when support already replied', async () => {
    const { value, queries } = deps({
      loadConversation: vi.fn(async () => ({ message: 'human', conversationContext: '', isAlreadyAnswered: true })),
    });
    await processHandoff(row(), value);
    expect(value.sendReply).not.toHaveBeenCalled();
    expect(statusOf(queries)).toEqual(['tagged_at', 'alerted_at', "ack_state = 'skipped'", "status = 'completed'"]);
  });

  it('falls back to the command language when the conversation cannot be loaded', async () => {
    const { value } = deps({ loadConversation: vi.fn(async () => { throw new Error('unavailable'); }) });
    await processHandoff(row({ customer_message: '人工' }), value);
    expect(vi.mocked(value.sendReply).mock.calls[0][1]).toContain('转接人工支持');
  });
});

function fakeClientPool(lockedAt: Date | null, failOn?: RegExp) {
  const sql: string[] = [];
  const params: unknown[][] = [];
  const client = {
    query: vi.fn(async (text: string, values: unknown[] = []) => {
      const flat = text.replace(/\s+/g, ' ').trim();
      sql.push(flat);
      params.push(values);
      if (failOn?.test(flat)) throw new Error('db down');
      if (flat.startsWith('SELECT human_cancelled_at')) return { rows: [{ human_cancelled_at: lockedAt }] };
      return { rows: [], rowCount: 1 };
    }),
    release: vi.fn(),
  };
  return { pool: { connect: vi.fn(async () => client) } as any, client, sql, params };
}

describe('recordWorkerHandoff', () => {
  const options = { jobId: 'job-1', ticketId: 5500, customerMessage: ' I want to talk to a human ', acknowledge: true, reason: 'test' };

  it('locks the ticket and records an acknowledged handoff', async () => {
    const { pool, client, sql, params } = fakeClientPool(null);
    await expect(recordWorkerHandoff(pool, options)).resolves.toBe('recorded');
    expect(sql[0]).toBe('BEGIN');
    expect(sql.at(-1)).toBe('COMMIT');
    expect(sql.some(q => q.startsWith('UPDATE ticket_states SET human_cancelled_at = NOW()'))).toBe(true);
    const eventIndex = sql.findIndex(q => q.startsWith('INSERT INTO events'));
    const handoffIndex = sql.findIndex(q => q.startsWith('INSERT INTO human_handoffs'));
    expect(eventIndex).toBeGreaterThan(0);
    expect(handoffIndex).toBeGreaterThan(eventIndex);
    expect(sql[eventIndex]).toContain("'internal'");
    expect(params[handoffIndex]).toEqual(['handoff:job-1', 5500, true, 'I want to talk to a human']);
    expect(client.release).toHaveBeenCalledOnce();
  });

  it('adds nothing when the ticket is already locked', async () => {
    const { pool, sql } = fakeClientPool(new Date());
    await expect(recordWorkerHandoff(pool, options)).resolves.toBe('already_locked');
    expect(sql.some(q => q.includes('human_handoffs') || q.startsWith('UPDATE ticket_states'))).toBe(false);
    expect(sql.at(-1)).toBe('COMMIT');
  });

  it('rolls back and rethrows on failure', async () => {
    const { pool, client, sql } = fakeClientPool(null, /^INSERT INTO human_handoffs/);
    await expect(recordWorkerHandoff(pool, options)).rejects.toThrow('db down');
    expect(sql.at(-1)).toBe('ROLLBACK');
    expect(client.release).toHaveBeenCalledOnce();
  });
});
