import fs from 'node:fs';
import ts from 'typescript';
import { afterEach, expect, it, vi } from 'vitest';

// Exercise the actual worker closure without importing its process startup,
// production DB connection, mention polling, or delivery services.
function workerJob(dependencies: Record<string, unknown>) {
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
