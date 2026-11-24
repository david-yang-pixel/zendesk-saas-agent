import { randomInt, randomUUID } from 'node:crypto';
import pg from 'pg';
import { afterAll, describe, expect, it, vi } from 'vitest';
import { claimNextJob, claimPublicDeliveryCheck, confirmPublicDelivery, deferUnconfirmedConversation, deliverWithCustomerCoverage, loadCustomerCoverage, recordStaffCoverage, requeueLatestConversation, ticketIdFromDatabase } from './job-queue.js';

it('normalizes PostgreSQL bigint ticket ids before returning a claimed job', async () => {
  const client = {
    query: vi.fn(async (sql: string) => ({ rows: sql.includes('SELECT job_id')
      ? [{ job_id: 'job-1', ticket_id: '3726', prev_status: 'queued', attempts: 0 }]
      : sql.includes('RETURNING attempts') ? [{ attempts: 1 }] : [] })),
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
  expect(queries[0]).toContain('lease_owner = NULL');
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
    if (sql.includes('SELECT generation, human_cancelled_at')) {
      return { rows: [{ generation: 3, human_cancelled_at: null }] };
    }
    if (sql.includes('SELECT generation, is_operator_mention')) {
      return { rows: jobs.filter(job => job.job_id === params[0] && job.ticket_id === params[1]) };
    }
    if (sql.includes('UPDATE case_jobs') && sql.includes("status = 'queued'")) {
      const queued = jobs.filter(job => job.ticket_id === params[0] && job.status === 'queued' &&
        (!sql.includes('NOT is_operator_mention') || !job.is_operator_mention));
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

it('does not coalesce a customer requeue into an operator mention', async () => {
  const mention = { ...interceptedJob, job_id: 'mention-job', event_id: 'mention:1', status: 'queued', is_operator_mention: true };
  const db = fakeCaseworkDb({ events: [interceptedJob.event_id, mention.event_id], jobs: [interceptedJob, mention] });
  const result = await requeueLatestConversation(db.pool as any, { job: interceptedJob, reason: 'new question' });
  expect(result.outcome).toBe('created');
  expect(db.jobs.find(job => job.job_id === mention.job_id)).toEqual(mention);
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

describe.skipIf(!process.env.TEST_DATABASE_URL)('PostgreSQL customer message coverage', () => {
  const pool = new pg.Pool({ connectionString: process.env.TEST_DATABASE_URL?.replace(/^postgresql\+\w+:\/\//, 'postgresql://') });
  const ticketIds: number[] = [];
  const workerId = 'coverage-test';

  afterAll(async () => {
    try {
      await pool.query('DELETE FROM run_records WHERE ticket_id = ANY($1::bigint[])', [ticketIds]);
      await pool.query('DELETE FROM public_deliveries WHERE ticket_id = ANY($1::bigint[])', [ticketIds]);
      await pool.query('DELETE FROM case_jobs WHERE ticket_id = ANY($1::bigint[])', [ticketIds]);
      await pool.query('DELETE FROM ticket_states WHERE ticket_id = ANY($1::bigint[])', [ticketIds]);
      await pool.query('DELETE FROM events WHERE ticket_id = ANY($1::bigint[])', [ticketIds]);
    } finally {
      await pool.end();
    }
  });

  async function createTicket() {
    const ticketId = randomInt(10 ** 12, 10 ** 13);
    ticketIds.push(ticketId);
    await pool.query('INSERT INTO ticket_states(ticket_id) VALUES ($1)', [ticketId]);
    return ticketId;
  }

  async function addJob(ticketId: number, status: 'running' | 'queued', mention = false) {
    const jobId = randomUUID();
    await pool.query(`
      INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, payload)
      VALUES ($1, $2, NOW(), 'test', 'internal', 'test', '{}')
    `, [jobId, ticketId]);
    await pool.query(`
      INSERT INTO case_jobs (job_id, session_id, event_id, expected_event_id, ticket_id, generation, status,
          attempts, available_at, lease_owner, lease_expires_at, is_operator_mention, config_revision,
          root_model_config_id, child_model_config_ids, model_digest, skill_digest, tool_digest)
      VALUES ($1, $1, $1, $1, $2, 1, $3, 1, NOW(), $4, NOW() + interval '3 minutes', $5,
          'test', 'test', '[]', 'test', 'test', 'test')
    `, [jobId, ticketId, status, status === 'running' ? workerId : null, mention]);
    return { job_id: jobId, ticket_id: ticketId };
  }

  async function admitMessage(ticketId: number, cursor: string) {
    await pool.query(`
      WITH next_sequence AS (
        UPDATE ticket_states SET latest_customer_seq = latest_customer_seq + 1
        WHERE ticket_id = $2 RETURNING latest_customer_seq
      )
      INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, message_id, customer_seq, payload)
      SELECT $1, $2, NOW(), 'ingress', 'customer', 'ticket.updated', $3, latest_customer_seq, '{}'
      FROM next_sequence
    `, [randomUUID(), ticketId, cursor]);
  }

  async function state(ticketId: number) {
    return (await pool.query('SELECT * FROM ticket_states WHERE ticket_id = $1', [ticketId])).rows[0];
  }

  it('keeps a job claimable after repeated delays in Zendesk history visibility', async () => {
    const ticketId = await createTicket();
    const job = await addJob(ticketId, 'running');
    await pool.query("UPDATE case_jobs SET attempts=3, created_at=NOW()-interval '2 minutes' WHERE job_id=$1", [job.job_id]);
    expect(await deferUnconfirmedConversation(pool, job, workerId, 3)).toBe(true);
    const { rows } = await pool.query('SELECT status, lease_owner, attempts, available_at > NOW() AS delayed FROM case_jobs WHERE job_id=$1', [job.job_id]);
    expect(rows[0]).toMatchObject({ status: 'queued', lease_owner: null, attempts: 2, delayed: true });
  });

  it('records staff coverage only for messages the worker observed before the staff reply', async () => {
    const ticketId = await createTicket();
    await admitMessage(ticketId, 'messaging:first');
    await admitMessage(ticketId, 'messaging:second');
    await recordStaffCoverage(pool, ticketId, ['messaging:first']);
    expect(await state(ticketId)).toMatchObject({ latest_customer_seq: '2', answered_customer_seq: '1', answered_by_job_id: null });
    expect((await loadCustomerCoverage(pool, ticketId)).pendingCursors).toEqual(new Set(['messaging:second']));
  });

  it('leaves a concurrent newer webhook pending after a confirmed send and preserves an operator job', async () => {
    const ticketId = await createTicket();
    await admitMessage(ticketId, 'messaging:read');
    const running = await addJob(ticketId, 'running');
    const mention = await addJob(ticketId, 'queued', true);
    const result = await deliverWithCustomerCoverage(pool, {
      job: running, workerId, attempt: 1, startingSeq: 1n, reply: 'First answer',
      messages: [{ cursors: ['messaging:read', 'zendesk:history-read-id'] }],
    }, async deliveryId => {
      const { rows } = await pool.query('SELECT * FROM public_deliveries WHERE delivery_id = $1', [deliveryId]);
      expect(rows[0]).toMatchObject({ status: 'pending', reply_body: 'First answer' });
      await admitMessage(ticketId, 'messaging:after-read');
      return { ok: true, status: 'sent', ticket_id: ticketId };
    });
    expect(result.outcome).toBe('delivered');
    expect(await state(ticketId)).toMatchObject({ latest_customer_seq: '2', answered_customer_seq: '1', answered_by_job_id: running.job_id });
    const coverage = await loadCustomerCoverage(pool, ticketId);
    expect(coverage.pending).toEqual([{ seq: 2n, cursor: 'messaging:after-read' }]);
    expect(coverage.coveredCursors).toEqual(new Set(['messaging:read', 'zendesk:history-read-id']));
    expect((await pool.query('SELECT status FROM case_jobs WHERE job_id = $1', [mention.job_id])).rows[0].status).toBe('queued');
  });

  it('takes no delivery action for an unread newer admission and never advances across a sequence gap', async () => {
    const ticketId = await createTicket();
    await admitMessage(ticketId, 'messaging:unconfirmed-first');
    await admitMessage(ticketId, 'messaging:known-second');
    const running = await addJob(ticketId, 'running');
    const send = vi.fn(async () => ({ ok: true, status: 'sent', ticket_id: ticketId }));
    const opts = { job: running, workerId, attempt: 1, reply: 'Second answer', messages: [{ cursors: ['messaging:known-second'] }] };
    expect(await deliverWithCustomerCoverage(pool, { ...opts, startingSeq: 0n }, send)).toEqual({ outcome: 'new_customer_message' });
    expect(send).not.toHaveBeenCalled();
    expect((await deliverWithCustomerCoverage(pool, { ...opts, startingSeq: 2n }, send)).outcome).toBe('delivered');
    expect(await state(ticketId)).toMatchObject({ latest_customer_seq: '2', answered_customer_seq: '0', answered_by_job_id: null });
    expect((await loadCustomerCoverage(pool, ticketId)).coveredCursors.has('messaging:known-second')).toBe(true);
  });

  it('rechecks handoff after reserving and reuses the delivery ID when a safe reservation is recovered', async () => {
    const ticketId = await createTicket();
    const job = await addJob(ticketId, 'running');
    await admitMessage(ticketId, 'messaging:read');
    const opts = { job, workerId, attempt: 1, startingSeq: 1n, reply: 'First answer',
      messages: [{ cursors: ['messaging:read'] }] };
    let connects = 0;
    const handoffPool = { connect: async () => {
      if (++connects === 2) await pool.query('UPDATE ticket_states SET human_cancelled_at = NOW() WHERE ticket_id = $1', [ticketId]);
      return pool.connect();
    } } as pg.Pool;
    const send = vi.fn(async (_id: string) => ({ ok: true, status: 'sent', ticket_id: ticketId }));
    expect(await deliverWithCustomerCoverage(handoffPool, opts, send)).toEqual({ outcome: 'superseded' });
    expect(send).not.toHaveBeenCalled();
    const reserved = (await pool.query('SELECT * FROM public_deliveries WHERE job_id = $1', [job.job_id])).rows[0];
    expect(reserved).toMatchObject({ status: 'reserved', started_at: null });
    await pool.query('UPDATE ticket_states SET human_cancelled_at = NULL WHERE ticket_id = $1', [ticketId]);
    expect((await deliverWithCustomerCoverage(pool, opts, send)).outcome).toBe('delivered');
    expect(send).toHaveBeenCalledExactlyOnceWith(reserved.delivery_id, opts.reply);
  });

  it('dispatches only the snapshot still stored by the reservation', async () => {
    const ticketId = await createTicket();
    const job = await addJob(ticketId, 'running');
    await admitMessage(ticketId, 'messaging:read');
    const opts = { job, workerId, attempt: 1, startingSeq: 1n, reply: 'Original answer',
      messages: [{ cursors: ['messaging:read'] }] };
    let connects = 0;
    const replacedReservationPool = { connect: async () => {
      if (++connects === 2) await pool.query(`UPDATE public_deliveries SET reply_body = 'Another answer'
        WHERE job_id = $1 AND status = 'reserved'`, [job.job_id]);
      return pool.connect();
    } } as pg.Pool;
    const send = vi.fn(async () => ({ ok: true, status: 'sent', ticket_id: ticketId }));
    expect(await deliverWithCustomerCoverage(replacedReservationPool, opts, send)).toEqual({ outcome: 'superseded' });
    expect(send).not.toHaveBeenCalled();
    expect(await state(ticketId)).toMatchObject({ answered_customer_seq: '0' });
  });

  it('allows handoff while a started HTTP call is pending and preserves cancellation after confirmation', async () => {
    const ticketId = await createTicket();
    const job = await addJob(ticketId, 'running');
    await admitMessage(ticketId, 'messaging:read');
    const abort = new AbortController();
    const result = await deliverWithCustomerCoverage(pool, {
      job, workerId, attempt: 1, startingSeq: 1n, reply: 'First answer', signal: abort.signal,
      messages: [{ cursors: ['messaging:read'] }],
    }, async () => {
      await pool.query('UPDATE ticket_states SET human_cancelled_at = NOW(), generation = 2 WHERE ticket_id = $1', [ticketId]);
      await pool.query("UPDATE case_jobs SET status = 'cancelled', dead_letter_reason = 'human_takeover' WHERE job_id = $1", [job.job_id]);
      abort.abort();
      return { ok: true, status: 'sent', ticket_id: ticketId };
    });
    expect(result).toMatchObject({ outcome: 'delivered', jobStatus: 'cancelled' });
    expect((await pool.query('SELECT status, dead_letter_reason FROM case_jobs WHERE job_id = $1', [job.job_id])).rows[0])
      .toEqual({ status: 'cancelled', dead_letter_reason: 'human_takeover' });
    expect(await state(ticketId)).toMatchObject({ generation: '2', answered_customer_seq: '1' });
  });

  it('fences an ambiguous send through lease expiry and retry, then reconciles only its reserved cursors', async () => {
    const ticketId = await createTicket();
    const job = await addJob(ticketId, 'running');
    await admitMessage(ticketId, 'messaging:read');
    const opts = { job, workerId, attempt: 1, startingSeq: 1n, reply: 'Answer',
      messages: [{ cursors: ['messaging:read', 'zendesk:history-read'] }] };
    const send = vi.fn(async () => { throw new Error('Socket reset after relay submission'); });
    const result = await deliverWithCustomerCoverage(pool, opts, send);
    expect(result.outcome).toBe('uncertain');
    expect(await state(ticketId)).toMatchObject({ answered_customer_seq: '0' });
    const check = await claimPublicDeliveryCheck(pool);
    expect(check).toMatchObject({ job_id: job.job_id, last_error: 'browser_relay_confirmation_missing' });
    expect(await claimPublicDeliveryCheck(pool)).toBeNull();

    // Incidents must remain fenced even if the historical admin retry SQL is used.
    await pool.query(`UPDATE case_jobs SET status = 'running', attempts = 3,
      lease_expires_at = NOW() - interval '10 minutes' WHERE job_id = $1`, [job.job_id]);
    expect((await claimNextJob(pool, 'other-worker'))?.job_id).not.toBe(job.job_id);
    expect((await pool.query('SELECT status FROM case_jobs WHERE job_id = $1', [job.job_id])).rows[0].status).toBe('running');
    await pool.query("UPDATE case_jobs SET status = 'queued' WHERE job_id = $1", [job.job_id]);
    expect((await claimNextJob(pool, 'other-worker'))?.job_id).not.toBe(job.job_id);
    await pool.query("UPDATE case_jobs SET status = 'failed' WHERE job_id = $1", [job.job_id]);
    await admitMessage(ticketId, 'messaging:new-follow-up');
    const successor = await addJob(ticketId, 'queued');
    expect((await claimNextJob(pool, 'other-worker'))?.job_id).not.toBe(successor.job_id);

    const evidence = { source: 'zendesk-conversation', message_id: 'positive-proof' } as const;
    await expect(confirmPublicDelivery(pool, check!, evidence)).resolves.toBe('completed');
    await expect(confirmPublicDelivery(pool, check!, evidence)).resolves.toBe('completed');
    expect(send).toHaveBeenCalledOnce();
    expect(await state(ticketId)).toMatchObject({ answered_customer_seq: '1', latest_customer_seq: '2' });
    expect((await loadCustomerCoverage(pool, ticketId)).pendingCursors).toEqual(new Set(['messaging:new-follow-up']));
    expect((await pool.query('SELECT outcome FROM run_records WHERE job_id = $1', [job.job_id])).rows[0].outcome.delivery)
      .toMatchObject({ status: 'sent', ...evidence });
  });

  it('coalesces concurrent requeues, records each observed identity, and retains the operator job', async () => {
    const ticketId = await createTicket();
    const running = await addJob(ticketId, 'running');
    const mention = await addJob(ticketId, 'queued', true);
    const results = await Promise.all(['a', 'b'].map(id => requeueLatestConversation(pool, {
      job: running, reason: 'new', lease: { workerId, attempt: 1 },
      observedCustomers: [{ cursors: [`messaging:${id}`] }],
    })));
    expect(results.map(result => result.outcome).sort()).toEqual(['coalesced', 'created']);
    expect(results[0].jobId).toBe(results[1].jobId);
    expect(results[0].jobId).not.toBe(mention.job_id);
    expect((await loadCustomerCoverage(pool, ticketId)).pendingCursors).toEqual(new Set(['messaging:a', 'messaging:b']));
  });

  it('allows only one concurrent claim for a ticket with customer and operator jobs ready', async () => {
    const ticketId = await createTicket();
    const customer = await addJob(ticketId, 'queued');
    const mention = await addJob(ticketId, 'queued', true);
    await pool.query(`UPDATE case_jobs SET available_at = NOW() - interval '1 day' WHERE ticket_id = $1`, [ticketId]);
    const claims = await Promise.all([claimNextJob(pool, workerId), claimNextJob(pool, workerId)]);
    const forTicket = claims.filter(job => job?.ticket_id === ticketId);
    expect(forTicket).toHaveLength(1);
    const statuses = (await pool.query(`SELECT job_id, status FROM case_jobs WHERE ticket_id = $1`, [ticketId])).rows;
    expect(statuses).toEqual(expect.arrayContaining([
      { job_id: forTicket[0].job_id, status: 'running' },
      { job_id: (forTicket[0].job_id === customer.job_id ? mention : customer).job_id, status: 'queued' },
    ]));
  });
});
