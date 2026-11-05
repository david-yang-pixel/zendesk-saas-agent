import { expect, it, vi } from 'vitest';
import { claimNextJob, requeueLatestConversation, ticketIdFromDatabase } from './job-queue.js';

it('normalizes PostgreSQL bigint ticket ids before returning a claimed job', async () => {
  const client = {
    query: vi.fn(async (sql: string) => ({ rows: sql.includes('SELECT job_id')
      ? [{ job_id: 'job-1', ticket_id: '3726', prev_status: 'queued', attempts: 0 }]
      : [] })),
    release: vi.fn(),
  };
  const job = await claimNextJob({ connect: async () => client } as any, 'worker-1');
  expect(job?.ticket_id).toBe(3726);
  expect(typeof job?.ticket_id).toBe('number');
  expect(client.query).toHaveBeenLastCalledWith('COMMIT');
});

it.each(['9007199254740992', '0', '-1', '1.5', '1e3', null, undefined])(
  'rejects unsafe or malformed database ticket id %s', value => {
    expect(() => ticketIdFromDatabase(value)).toThrow('Invalid ticket_id');
  },
);

it('rolls back when a claimed job has an unsafe ticket id', async () => {
  const client = {
    query: vi.fn(async (sql: string) => ({ rows: sql.includes('SELECT job_id')
      ? [{ job_id: 'job-1', ticket_id: '9007199254740992', prev_status: 'queued' }]
      : [] })),
    release: vi.fn(),
  };
  await expect(claimNextJob({ connect: async () => client } as any, 'worker-1'))
    .rejects.toThrow('Invalid ticket_id');
  expect(client.query).toHaveBeenLastCalledWith('ROLLBACK');
  expect(client.release).toHaveBeenCalledOnce();
});
it('commits expired-job cleanup even when no job can be claimed', async () => {
  const client = { query: vi.fn(async () => ({ rows: [] })), release: vi.fn() };
  const pool = { connect: vi.fn(async () => client) };
  expect(await claimNextJob(pool as any, 'test')).toBeNull();
  const queries = client.query.mock.calls.map(call => call[0]);
  expect(queries[1]).toContain('lease_owner = NULL');
  expect(queries.at(-1)).toBe('COMMIT');
  expect(queries).not.toContain('ROLLBACK');
  expect(client.release).toHaveBeenCalledOnce();
});
it('rolls back and releases the client when claiming fails', async () => {
  const client = { query: vi.fn().mockResolvedValueOnce({ rows: [] }).mockRejectedValueOnce(new Error('DB failure')).mockResolvedValue({ rows: [] }), release: vi.fn() };
  await expect(claimNextJob({ connect: async () => client } as any, 'test')).rejects.toThrow('DB failure');
  expect(client.query).toHaveBeenLastCalledWith('ROLLBACK');
  expect(client.release).toHaveBeenCalledOnce();
});

// Minimal stand-in for the casework tables that enforces the constraints the
// production incident (#5352) tripped: case_jobs.event_id -> events.event_id,
// unique case_jobs.event_id, and one queued job per ticket.
function fakeCaseworkDb(opts: { events?: string[]; jobs?: Record<string, any>[] } = {}) {
  const events = new Map<string, Record<string, any>>((opts.events ?? []).map(id => [id, { event_id: id }]));
  const jobs: Record<string, any>[] = (opts.jobs ?? []).map(job => ({ ...job }));
  const statements: string[] = [];
  const query = vi.fn(async (sql: string, params: any[] = []) => {
    statements.push(sql.trim().split(/\s+/).slice(0, 3).join(' '));
    if (/^(BEGIN|COMMIT|ROLLBACK)$/.test(sql.trim())) return { rows: [], rowCount: 0 };
    if (sql.includes('UPDATE case_jobs') && sql.includes("status = 'queued'")) {
      const queued = jobs.filter(job => job.ticket_id === params[0] && job.status === 'queued');
      return { rows: queued.map(job => ({ job_id: job.job_id })), rowCount: queued.length };
    }
    if (sql.includes('INSERT INTO events')) {
      if (events.has(params[0])) return { rows: [], rowCount: 0 };
      events.set(params[0], { event_id: params[0], ticket_id: params[1], customer_origin: sql.match(/'(internal|customer)'/)?.[1] });
      return { rows: [], rowCount: 1 };
    }
    if (sql.includes('INSERT INTO case_jobs')) {
      const [jobId, sessionId, eventId, expectedEventId, previousJobId] = params;
      if (!events.has(eventId)) {
        throw new Error('insert or update on table "case_jobs" violates foreign key constraint "case_jobs_event_id_fkey"');
      }
      if (jobs.some(job => job.event_id === eventId)) {
        throw new Error('duplicate key value violates unique constraint "uq_case_jobs_event_id"');
      }
      const previous = jobs.find(job => job.job_id === previousJobId);
      if (!previous) return { rows: [], rowCount: 0 };
      const job = { ...previous, job_id: jobId, session_id: sessionId, event_id: eventId,
        expected_event_id: expectedEventId, status: 'queued', attempts: 0 };
      jobs.push(job);
      return { rows: [{ job_id: jobId }], rowCount: 1 };
    }
    throw new Error(`unexpected SQL in fake casework db: ${sql}`);
  });
  const client = { query, release: vi.fn() };
  return { events, jobs, statements, client, pool: { connect: vi.fn(async () => client), query } };
}

const interceptedJob = {
  job_id: '880b1790-intercepted', ticket_id: 5352, generation: 3,
  event_id: 'webhook-uuid-1', expected_event_id: 'webhook-uuid-1', status: 'running',
  config_revision: 'rev-42', root_model_config_id: 'root-model', child_model_config_ids: ['child'],
  model_digest: 'md', skill_digest: 'sd', tool_digest: 'td',
};

it('requeues an intercepted conversation against a recorded local event, not the Zendesk comment id', async () => {
  // Zendesk already shows the new customer comment, but ingress has not
  // recorded its webhook yet, so no local event row exists for it.
  const db = fakeCaseworkDb({ events: ['webhook-uuid-1'], jobs: [interceptedJob] });
  const result = await requeueLatestConversation(db.pool as any, {
    job: interceptedJob, latestZendeskEventId: '41234567890', reason: 'new customer message',
  });

  expect(result.outcome).toBe('created');
  const successor = db.jobs.find(job => job.job_id === result.jobId)!;
  expect(successor).toMatchObject({
    status: 'queued', ticket_id: 5352, generation: 3,
    expected_event_id: '41234567890',
    config_revision: 'rev-42', root_model_config_id: 'root-model', model_digest: 'md',
  });
  expect(successor.event_id).not.toBe('41234567890');
  expect(db.events.get(successor.event_id)).toMatchObject({ ticket_id: 5352, customer_origin: 'internal' });
  expect(db.client.query).toHaveBeenLastCalledWith('COMMIT');
  expect(db.client.release).toHaveBeenCalledOnce();
});

it('reuses an already queued successor instead of inserting a second job', async () => {
  const queued = { ...interceptedJob, job_id: 'ingress-queued', event_id: 'webhook-uuid-2', status: 'queued' };
  const db = fakeCaseworkDb({ events: ['webhook-uuid-1', 'webhook-uuid-2'], jobs: [interceptedJob, queued] });
  const result = await requeueLatestConversation(db.pool as any, {
    job: interceptedJob, latestZendeskEventId: '41234567890', reason: 'new customer message',
  });
  expect(result).toEqual({ outcome: 'coalesced', jobId: 'ingress-queued' });
  expect(db.jobs).toHaveLength(2);
  expect(db.events.size).toBe(2);
  expect(db.client.query).toHaveBeenLastCalledWith('COMMIT');
});

it('records a distinct event each time the same job is intercepted again', async () => {
  const db = fakeCaseworkDb({ events: ['webhook-uuid-1'], jobs: [interceptedJob] });
  const first = await requeueLatestConversation(db.pool as any, { job: interceptedJob, reason: 'first' });
  db.jobs.find(job => job.job_id === first.jobId)!.status = 'cancelled';
  const second = await requeueLatestConversation(db.pool as any, { job: interceptedJob, reason: 'second' });
  expect(second.outcome).toBe('created');
  expect(second.jobId).not.toBe(first.jobId);
});

it('rolls back the requeue when the intercepted job row is gone', async () => {
  const db = fakeCaseworkDb({ events: [], jobs: [] });
  await expect(requeueLatestConversation(db.pool as any, { job: interceptedJob, reason: 'gone' }))
    .rejects.toThrow('not found');
  expect(db.client.query).toHaveBeenLastCalledWith('ROLLBACK');
  expect(db.client.release).toHaveBeenCalledOnce();
});
