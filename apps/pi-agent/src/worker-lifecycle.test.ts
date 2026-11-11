import fs from 'node:fs';
import ts from 'typescript';
import { afterEach, expect, it, vi } from 'vitest';
import { formatInternalDraft } from './internal-draft-delivery.js';
import { detectCustomerLanguage } from './reply-policy.js';
import { requeueLatestConversation } from './job-queue.js';

// Exercise the actual worker closure without importing its process startup,
// production DB connection, mention polling, or delivery services.
function workerJob(overrides: Record<string, unknown>) {
  const dependencies = { formatInternalDraft, detectCustomerLanguage, requeueLatestConversation, ...overrides };
  const source = fs.readFileSync(new URL('./worker.ts', import.meta.url), 'utf8');
  const start = source.indexOf('  async function processJob(');
  const end = source.indexOf('\n  while (running)', start);
  if (start < 0 || end < 0) throw new Error('Worker lifecycle test boundary changed');
  const js = ts.transpileModule(source.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return new Function(...Object.keys(dependencies), `${js}; return processJob;`)(...Object.values(dependencies));
}
afterEach(() => vi.useRealTimers());
it('webhook UUID jobs load instructions by event ID and bypass customer-only guards without logging the note', async () => {
  vi.useFakeTimers();
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  const query = vi.fn(async () => ({ rows: [{ payload: { body: '@agent private-test-note' } }] }));
  const runPiSupportAgent = vi.fn(async () => { throw new Error('test-stop'); });
  const markJobCancelled = vi.fn();
  const process = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2, pool: { query },
    isTicketHumanCancelled: async () => true,
    getTicketDetails: async () => ({ isAlreadyAnswered: true, hasRequestedHuman: true, message: 'customer' }),
    getActiveModelConfig: async () => ({ model: 'test' }),
    openConnectorService: {}, allTools: [], runPiSupportAgent,
    recordJobOutcome: vi.fn(), markJobFailed: vi.fn(), markJobCancelled,
    sendFeishuAlert: vi.fn(), FEISHU_WEBHOOK_URL: 'test',
  });
  try {
    await process({ job_id: 'test', ticket_id: 99999, event_id: 'uuid-webhook', expected_event_id: 'mention:1234' }, new AbortController());
    expect(query).toHaveBeenCalledWith('SELECT payload FROM events WHERE event_id = $1 LIMIT 1', ['uuid-webhook']);
    expect(runPiSupportAgent).toHaveBeenCalledWith(expect.objectContaining({ customerMessage: expect.stringContaining('@agent private-test-note') }));
    expect(markJobCancelled).not.toHaveBeenCalled();
    expect(JSON.stringify(log.mock.calls)).not.toContain('private-test-note');
    expect(vi.getTimerCount()).toBe(0);
  } finally { log.mockRestore(); }
});
it('records a context-read failure, marks the job failed, and clears its lease heartbeat', async () => {
  vi.useFakeTimers();
  const recordJobOutcome = vi.fn();
  const markJobFailed = vi.fn();
  const sendFeishuAlert = vi.fn();
  const job = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2, pool: { query: vi.fn() },
    isTicketHumanCancelled: vi.fn(async () => false),
    getTicketDetails: vi.fn(async () => { throw new Error('context unavailable'); }),
    openConnectorService: {}, recordJobOutcome, markJobFailed, sendFeishuAlert, FEISHU_WEBHOOK_URL: 'test',
  });
  await job({ job_id: 'test-job', ticket_id: 3726, event_id: 'mention:test' }, new AbortController());
  expect(recordJobOutcome).toHaveBeenCalledWith(expect.objectContaining({ status: 'failed', errorReason: 'context unavailable' }));
  expect(markJobFailed).toHaveBeenCalledWith('test-job', 'context unavailable');
  expect(sendFeishuAlert).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});
it('clears the heartbeat when a duplicate-reply guard cancels before analysis', async () => {
  vi.useFakeTimers();
  const markJobCancelled = vi.fn();
  const markJobFailed = vi.fn();
  const job = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2, pool: { query: vi.fn() },
    isTicketHumanCancelled: vi.fn(async () => false),
    getTicketDetails: vi.fn(async () => ({ isAlreadyAnswered: true })),
    openConnectorService: {}, markJobCancelled, markJobFailed,
  });
  await job({ job_id: 'test-job', ticket_id: 3726 }, new AbortController());
  expect(markJobCancelled).toHaveBeenCalledOnce();
  expect(markJobFailed).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it('uses two context reads before delivering a customer-origin public reply', async () => {
  vi.useFakeTimers();
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  const getTicketDetails = vi.fn(async () => ({
    isAlreadyAnswered: false, hasRequestedHuman: false,
    message: 'hello', lastEventId: 'customer:1234', customerEventsCount: 1,
  }));
  const query = vi.fn(async (sql: string) => sql.startsWith('SELECT payload')
    ? { rows: [{ payload: { body: '@agent reply to the customer' } }] }
    : { rows: [], rowCount: 0 });
  const sendBrowserRelay = vi.fn(async () => ({ status: 'sent', total_duration_ms: 5 }));
  const markJobSucceeded = vi.fn();
  const markJobFailed = vi.fn();
  const process = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2, pool: { query },
    isTicketHumanCancelled: async () => false, getTicketDetails,
    getActiveModelConfig: async () => ({ model: 'test' }),
    openConnectorService: {}, allTools: [],
    runPiSupportAgent: async () => ({
      reply: 'safe reply', trace: { toolCalls: [], rounds: [], totalDurationMs: 1 },
    }),
    sendBrowserRelay, BROWSER_RELAY_URL: 'test', BROWSER_RELAY_KEY: 'test',
    recordJobOutcome: vi.fn(), markJobSucceeded, markJobFailed,
    sendFeishuAlert: vi.fn(), FEISHU_WEBHOOK_URL: 'test',
  });
  try {
    await process({
      job_id: 'test-job', ticket_id: 3726, generation: 1,
      event_id: 'webhook-uuid', expected_event_id: 'customer:1234',
    }, new AbortController());
    expect(getTicketDetails).toHaveBeenCalledTimes(2);
    expect(sendBrowserRelay).toHaveBeenCalledOnce();
    expect(markJobSucceeded).toHaveBeenCalledOnce();
    expect(markJobFailed).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    log.mockRestore();
  }
});

it('writes the English customer draft as an internal note for a mention, never as a public reply', async () => {
  vi.useFakeTimers();
  const query = vi.fn(async (sql: string) => sql.startsWith('SELECT payload')
    ? { rows: [{ payload: { body: '@David Yang 看下这个工单用户的教育积分情况' } }] }
    : { rows: [], rowCount: 0 });
  const sendBrowserRelay = vi.fn();
  const deliverInternalDraft = vi.fn(async () => ({ channel: 'zendesk-internal-note', status: 'sent' }));
  const recordJobOutcome = vi.fn();
  const markJobSucceeded = vi.fn();
  const runPiSupportAgent = vi.fn(async () => ({
    reply: '', customerReply: 'We are reviewing the current credits policy.', internalNote: 'private analysis',
    operatorAnswer: '该工作区在教育优惠期内，积分是一次性 200，不按月刷新。',
    trace: { toolCalls: [], rounds: [], totalDurationMs: 1, finalReply: '' },
  }));
  const process = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2, pool: { query },
    isTicketHumanCancelled: async () => false,
    getTicketDetails: async () => ({ isAlreadyAnswered: true, hasRequestedHuman: false, message: 'customer' }),
    getActiveModelConfig: async () => ({ model: 'test' }),
    openConnectorService: {}, allTools: [], runPiSupportAgent,
    sendBrowserRelay, deliverInternalDraft, recordJobOutcome, markJobSucceeded, markJobFailed: vi.fn(),
    ZENDESK_BASE_URL: 'https://example.zendesk.com', ZENDESK_EMAIL: 'agent@example.com', ZENDESK_API_TOKEN: 'test-token',
    sendFeishuAlert: vi.fn(), FEISHU_WEBHOOK_URL: 'test',
  });
  await process({ job_id: 'test-job', ticket_id: 3726, generation: 1, event_id: 'mention:test' }, new AbortController());
  expect(runPiSupportAgent).toHaveBeenCalledWith(expect.objectContaining({ publicReplyAllowed: false }));
  expect(sendBrowserRelay).not.toHaveBeenCalled();
  expect(deliverInternalDraft).toHaveBeenCalledWith(expect.objectContaining({
    jobId: 'test-job', ticketId: 3726,
    customerReply: 'We are reviewing the current credits policy.',
    operatorAnswer: '该工作区在教育优惠期内，积分是一次性 200，不按月刷新。',
  }));
  expect(deliverInternalDraft.mock.calls[0]?.[0]).not.toHaveProperty('internalNote');
  expect(recordJobOutcome).toHaveBeenCalledWith(expect.objectContaining({
    status: 'completed', delivery: { channel: 'zendesk-internal-note', status: 'sent' },
    internalDraft: expect.stringMatching(/^AI 调查结论（仅内部可见）\n\n该工作区在教育优惠期内.*\n---\n客户回复草稿（英文，未发送给客户）：\n\nWe are reviewing the current credits policy\.$/s),
  }));
  expect(markJobSucceeded).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it('does not treat an explicit public-reply request inside an operator mention as public authorization', async () => {
  vi.useFakeTimers();
  const sendBrowserRelay = vi.fn();
  const deliverInternalDraft = vi.fn(async () => ({ channel: 'zendesk-internal-note', status: 'sent' }));
  const runPiSupportAgent = vi.fn(async () => ({
    reply: '', customerReply: 'Hello, here is the draft.',
    trace: { toolCalls: [], rounds: [], totalDurationMs: 1, finalReply: '' },
  }));
  const job = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2,
    pool: { query: vi.fn(async () => ({ rows: [{ payload: { body: '@agent 请公开回复客户' } }] })) },
    isTicketHumanCancelled: async () => false,
    getTicketDetails: async () => ({ message: 'hello', isAlreadyAnswered: false, hasRequestedHuman: false }),
    getActiveModelConfig: async () => ({ model: 'test' }), openConnectorService: {}, allTools: [],
    runPiSupportAgent, sendBrowserRelay, deliverInternalDraft,
    ZENDESK_BASE_URL: 'https://example.zendesk.com', ZENDESK_EMAIL: 'agent@example.com', ZENDESK_API_TOKEN: 'test-token',
    recordJobOutcome: vi.fn(), markJobSucceeded: vi.fn(), markJobFailed: vi.fn(),
    sendFeishuAlert: vi.fn(), FEISHU_WEBHOOK_URL: 'test',
  });
  await job({ job_id: 'test-job', ticket_id: 3726, event_id: 'mention:1234' }, new AbortController());
  expect(runPiSupportAgent).toHaveBeenCalledWith(expect.objectContaining({ publicReplyAllowed: false }));
  expect(deliverInternalDraft).toHaveBeenCalledOnce();
  expect(sendBrowserRelay).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

// Enforces case_jobs.event_id -> events.event_id like production, which is
// exactly the constraint ticket #5352 violated on requeue.
function fkEnforcingPool(knownEvents: string[], runningJob: Record<string, unknown>) {
  const events = new Set(knownEvents);
  const jobs: Record<string, any>[] = [{ ...runningJob, status: 'running' }];
  const query = vi.fn(async (sql: string, params: any[] = []) => {
    if (/^(BEGIN|COMMIT|ROLLBACK)$/.test(sql.trim())) return { rows: [], rowCount: 0 };
    if (sql.includes('INSERT INTO events')) {
      events.add(params[0]);
      return { rows: [], rowCount: 1 };
    }
    if (sql.includes('INSERT INTO case_jobs')) {
      const eventId = params[2];
      if (!events.has(eventId)) {
        throw new Error('insert or update on table "case_jobs" violates foreign key constraint "case_jobs_event_id_fkey"');
      }
      jobs.push({ job_id: params[0], event_id: eventId, expected_event_id: params[3], ticket_id: runningJob.ticket_id, status: 'queued' });
      return { rows: [{ job_id: params[0] }], rowCount: 1 };
    }
    if (sql.includes('UPDATE case_jobs') && sql.includes("status = 'queued'")) {
      const queued = jobs.filter(job => job.status === 'queued');
      return { rows: queued.map(job => ({ job_id: job.job_id })), rowCount: queued.length };
    }
    return { rows: [], rowCount: 0 };
  });
  return { events, jobs, pool: { query, connect: async () => ({ query, release: () => {} }) } };
}

const ticket5352Job = { job_id: '880b1790-test', ticket_id: 5352, generation: 1, event_id: 'webhook-uuid-1', expected_event_id: 'webhook-uuid-1' };
const beforeReasoning = { isAlreadyAnswered: false, hasRequestedHuman: false, message: 'first question', lastEventId: '41230000001', customerEventsCount: 1 };
// The new comment is visible in Zendesk before ingress has recorded its webhook.
const afterReasoning = { isAlreadyAnswered: false, hasRequestedHuman: false, message: 'Conversation with n77 follow-up', lastEventId: '41230000002', customerEventsCount: 2 };

function interceptionHarness(overrides: Record<string, unknown> = {}) {
  const db = fkEnforcingPool(['webhook-uuid-1'], ticket5352Job);
  const spies = {
    recordJobOutcome: vi.fn(), markJobCancelled: vi.fn(), markJobFailed: vi.fn(), markJobSucceeded: vi.fn(),
    sendFeishuAlert: vi.fn(), sendBrowserRelay: vi.fn(),
  };
  const getTicketDetails = vi.fn()
    .mockResolvedValueOnce(beforeReasoning)
    .mockResolvedValue(afterReasoning);
  const job = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2, pool: db.pool,
    isTicketHumanCancelled: async () => false, getTicketDetails,
    getActiveModelConfig: async () => ({ model: 'test' }), openConnectorService: {}, allTools: [],
    runPiSupportAgent: async () => ({ reply: 'stale reply', trace: { toolCalls: [], rounds: [], totalDurationMs: 1, finalReply: 'stale reply' } }),
    BROWSER_RELAY_URL: 'test', BROWSER_RELAY_KEY: 'test', FEISHU_WEBHOOK_URL: 'test',
    ...spies, ...overrides,
  });
  return { db, job, ...spies };
}

function expectCancelledNotFailed(h: ReturnType<typeof interceptionHarness>) {
  expect(h.sendBrowserRelay).not.toHaveBeenCalled();
  expect(h.markJobCancelled).toHaveBeenCalledOnce();
  expect(h.markJobFailed).not.toHaveBeenCalled();
  expect(h.recordJobOutcome).toHaveBeenCalledTimes(1);
  expect(h.recordJobOutcome).toHaveBeenCalledWith(expect.objectContaining({ status: 'cancelled' }));
  expect(h.sendFeishuAlert).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ title: expect.stringContaining('失败警告') }));
  expect(vi.getTimerCount()).toBe(0);
}

it('ticket #5352: pre-delivery interception requeues against a recorded event and stays cancelled', async () => {
  vi.useFakeTimers();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  try {
    const h = interceptionHarness();
    await h.job(ticket5352Job, new AbortController());
    expectCancelledNotFailed(h);
    expect(h.sendFeishuAlert).not.toHaveBeenCalled();
    const successor = h.db.jobs.find(job => job.status === 'queued');
    expect(successor).toBeDefined();
    expect(h.db.events.has(successor!.event_id)).toBe(true);
    expect(successor!.event_id).not.toBe('41230000002');
    expect(successor!.expected_event_id).toBe('41230000002');
  } finally { warn.mockRestore(); log.mockRestore(); }
});

function failingRequeueHarness(overrides: Record<string, unknown> = {}) {
  const h = interceptionHarness({
    requeueLatestConversation: vi.fn(async () => { throw new Error('connection terminated'); }),
    ...overrides,
  });
  // Also fail any direct INSERT so a requeue that bypasses the helper fails too.
  const query = h.db.pool.query;
  h.db.pool.query = vi.fn(async (sql: string, params?: any[]) => {
    if (sql.includes('INSERT INTO case_jobs')) throw new Error('connection terminated');
    return query(sql, params);
  });
  return h;
}

it('keeps an intercepted job cancelled and sends only a low-priority requeue alert when the requeue fails', async () => {
  vi.useFakeTimers();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  try {
    const h = failingRequeueHarness();
    await h.job(ticket5352Job, new AbortController());
    expectCancelledNotFailed(h);
    expect(h.markJobCancelled.mock.calls[0]?.[1]).toContain('connection terminated');
    expect(h.sendFeishuAlert).toHaveBeenCalledOnce();
    const [, alert] = h.sendFeishuAlert.mock.calls[0]!;
    expect(alert).toMatchObject({
      title: 'ℹ️ Dify 工单重新排队失败提醒（低优先级）',
      ticketId: 5352,
      reason: 'connection terminated',
      customerMessage: 'Conversation with n77 follow-up',
    });
    expect(alert.status).toContain('已取消');
    expect(alert).not.toHaveProperty('draftReply');
  } finally { warn.mockRestore(); error.mockRestore(); log.mockRestore(); }
});

it('still cancels the intercepted job when the requeue alert itself throws', async () => {
  vi.useFakeTimers();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  try {
    const h = failingRequeueHarness();
    h.sendFeishuAlert.mockRejectedValue(new Error('feishu down'));
    await h.job(ticket5352Job, new AbortController());
    expectCancelledNotFailed(h);
    expect(h.sendFeishuAlert).toHaveBeenCalledOnce();
  } finally { warn.mockRestore(); error.mockRestore(); log.mockRestore(); }
});

it('in-flight abort for a new customer message requeues against a recorded event and stays cancelled', async () => {
  vi.useFakeTimers();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  try {
    const abortController = new AbortController();
    const h = interceptionHarness({
      runPiSupportAgent: async () => {
        abortController.abort();
        const err = new Error('aborted'); err.name = 'AbortError'; throw err;
      },
    });
    await h.job(ticket5352Job, abortController);
    expectCancelledNotFailed(h);
    expect(h.sendFeishuAlert).not.toHaveBeenCalled();
    const successor = h.db.jobs.find(job => job.status === 'queued');
    expect(successor).toBeDefined();
    expect(h.db.events.has(successor!.event_id)).toBe(true);
  } finally { warn.mockRestore(); log.mockRestore(); }
});

it('hands a human request found by the worker to the handoff queue and cancels the job', async () => {
  vi.useFakeTimers();
  const handOffToHuman = vi.fn(async () => {});
  const markJobCancelled = vi.fn();
  const details = { isAlreadyAnswered: false, hasRequestedHuman: true, latestRequestedHuman: true, message: 'I want to talk to a human' };
  const job = workerJob({
    activeJobs: new Map(), MAX_CONCURRENT_JOBS: 2, pool: { query: vi.fn() },
    isTicketHumanCancelled: vi.fn(async () => false),
    getTicketDetails: vi.fn(async () => details),
    openConnectorService: {}, handOffToHuman, markJobCancelled,
    runPiSupportAgent: vi.fn(), recordJobOutcome: vi.fn(), markJobFailed: vi.fn(),
    sendFeishuAlert: vi.fn(), FEISHU_WEBHOOK_URL: 'test',
  });
  await job({ job_id: 'job-h', ticket_id: 5500, event_id: 'evt-1', expected_event_id: 'evt-1' }, new AbortController());
  expect(handOffToHuman).toHaveBeenCalledWith(expect.objectContaining({ job_id: 'job-h' }), details, expect.stringContaining('人工'));
  expect(markJobCancelled).toHaveBeenCalledWith('job-h', expect.stringContaining('人工'));
  expect(vi.getTimerCount()).toBe(0);
});
