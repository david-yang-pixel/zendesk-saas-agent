import { randomInt, randomUUID } from 'node:crypto';
import pg from 'pg';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import {
  getEndpointClaimDelay, isModelQuotaError, modelEndpointKey, postponeJobForModelQuota,
  prepareModelEndpoint, recordModelRecovery, type RunningJobLease,
} from './endpoint-cooldown.js';

it('keys cooldowns by normalized model base URL', () => {
  const endpoint = modelEndpointKey('https://api.example.test/v1');
  expect(modelEndpointKey(' HTTPS://user:secret@API.EXAMPLE.TEST:443/v1/?token=secret#fragment ')).toBe(endpoint);
  expect(modelEndpointKey('https://api.example.test')).toBe(endpoint);
  expect(modelEndpointKey('https://api.example.test/v2')).not.toBe(endpoint);
  expect(modelEndpointKey('https://other.example.test/v1')).not.toBe(endpoint);
  expect(endpoint).toMatch(/^[a-f0-9]{64}$/);
});

it.each([
  { status: 402 }, { statusCode: '402' }, { response: { status: 402 } },
  new Error('Pi Agent model request failed for ticket #123: 402 {"error":"insufficient credits"}'),
  new Error('Pi Agent model request failed for ticket #123: OpenAI API error (402): insufficient credits'),
  new Error('HTTP 402: Payment Required'),
])('detects explicit provider status 402 in %s', failure => {
  expect(isModelQuotaError(failure)).toBe(true);
});

it.each([
  undefined, { status: 429, message: '402: sample' }, new Error('4020: payment unavailable'),
  new Error('Pi Agent model request failed for ticket #402: 400: invalid request'),
  new Error('Pi Agent model request failed for ticket #123: 500: upstream 402'),
  new Error('Ticket #402 has insufficient quota'),
])('keeps non-402 failures on the normal error path for %s', failure => {
  expect(isModelQuotaError(failure)).toBe(false);
});

// Run against an explicit test database migrated through 0013. Exercise the
// actual partial index, row and advisory locks, and transaction boundaries.
const databaseUrl = process.env.TEST_DATABASE_URL;
describe.skipIf(!databaseUrl)('PostgreSQL endpoint cooldown', () => {
  let pool: pg.Pool;
  const ticketIds = new Set<number>();
  const keys = new Set<string>();

  beforeAll(() => {
    pool = new pg.Pool({ connectionString: databaseUrl?.replace(/^postgresql\+\w+:\/\//, 'postgresql://') });
  });

  afterAll(async () => {
    try {
      await pool.query('DELETE FROM case_jobs WHERE ticket_id = ANY($1::bigint[])', [[...ticketIds]]);
      await pool.query('DELETE FROM ticket_states WHERE ticket_id = ANY($1::bigint[])', [[...ticketIds]]);
      await pool.query('DELETE FROM events WHERE ticket_id = ANY($1::bigint[])', [[...ticketIds]]);
      await pool.query('DELETE FROM model_endpoint_cooldowns WHERE endpoint_key = ANY($1::text[])', [[...keys]]);
    } finally {
      await pool.end();
    }
  });

  function endpoint() {
    const key = modelEndpointKey(`https://model.example.test/${randomUUID()}`);
    keys.add(key);
    return key;
  }

  async function seedJob(options: {
    ticketId?: number; queued?: boolean; mention?: boolean; generation?: number;
  } = {}): Promise<RunningJobLease> {
    const ticketId = options.ticketId ?? randomInt(10 ** 12, 10 ** 13);
    ticketIds.add(ticketId);
    const jobId = `quota-test-${randomUUID()}`;
    const leaseOwner = randomUUID();
    const eventId = `event-${jobId}`;
    await pool.query(`
      INSERT INTO ticket_states (ticket_id, latest_customer_seq) VALUES ($1, 4)
      ON CONFLICT (ticket_id) DO NOTHING
    `, [ticketId]);
    await pool.query(`
      INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, payload)
      VALUES ($1, $2, NOW(), 'test', $3, 'comment', '{"body":"keep this event"}')
    `, [eventId, ticketId, options.mention ? 'internal' : 'customer']);
    await pool.query(`
      INSERT INTO case_jobs (job_id, ticket_id, generation, event_id, expected_event_id, session_id,
        status, attempts, available_at, lease_owner, lease_expires_at, heartbeat_at,
        is_operator_mention, config_revision, root_model_config_id, child_model_config_ids,
        model_digest, skill_digest, tool_digest)
      VALUES ($1, $2, $3, $4, $5, $1, $6, $7, NOW() - interval '10 seconds', $8,
        CASE WHEN $8::varchar IS NOT NULL THEN NOW() + interval '3 minutes' END, NOW(),
        $9, 'rev', 'model', '[]', 'm', 's', 't')
    `, [jobId, ticketId, options.generation ?? 1, eventId, `cursor-${jobId}`,
      options.queued ? 'queued' : 'running', options.queued ? 0 : 3,
      options.queued ? null : leaseOwner, Boolean(options.mention)]);
    return { jobId, ticketId, leaseOwner };
  }

  async function row(job: RunningJobLease) {
    return (await pool.query('SELECT * FROM case_jobs WHERE job_id = $1', [job.jobId])).rows[0];
  }

  async function claimDelay(key: string) {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      return await getEndpointClaimDelay(client, key);
    } finally {
      await client.query('ROLLBACK');
      client.release();
    }
  }

  it('opens one fixed window for concurrent 402s and refunds both claims', async () => {
    const key = endpoint();
    const jobs = await Promise.all([seedJob(), seedJob()]);
    expect(await claimDelay(key)).toBeNull();
    for (const job of jobs) {
      expect(await prepareModelEndpoint(pool, key, job)).toEqual({ outcome: 'ready', observedWindowId: null });
    }
    const results = await Promise.all(jobs.map(job => postponeJobForModelQuota(pool, key, job, null)));
    expect(results.filter(result => result.shouldAlert)).toHaveLength(1);
    expect(results[0].retryAt).toEqual(results[1].retryAt);
    expect(await claimDelay(key)).toEqual(results[0].retryAt);
    expect(await claimDelay(endpoint())).toBeNull();
    for (const job of jobs) {
      expect(await row(job)).toMatchObject({ status: 'queued', attempts: 2, lease_owner: null,
        lease_expires_at: null, heartbeat_at: null, finished_at: null });
    }
    expect(await postponeJobForModelQuota(pool, key, jobs[0], null)).toEqual({
      outcome: 'lease_lost', retryAt: null, shouldAlert: false,
    });
  });

  it('serializes cooldown opening with a claim that has passed the gate', async () => {
    const key = endpoint();
    const running = await seedJob();
    const claim = await pool.connect();
    const writer = await pool.connect();
    let write: ReturnType<typeof postponeJobForModelQuota> | undefined;
    try {
      const claimPid = (await claim.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
      const writerPid = (await writer.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
      await claim.query('BEGIN');
      expect(await getEndpointClaimDelay(claim, key)).toBeNull();
      write = postponeJobForModelQuota({ connect: async () => writer }, key, running, null);
      await vi.waitFor(async () => {
        const blockers = await pool.query('SELECT pg_blocking_pids($1) AS pids', [writerPid]);
        expect(blockers.rows[0].pids).toContain(claimPid);
      });
      await claim.query('COMMIT');
      const result = await write;
      expect(result).toMatchObject({ outcome: 'requeued', shouldAlert: true });
      expect(await claimDelay(key)).toEqual(result.retryAt);
    } finally {
      await claim.query('ROLLBACK');
      claim.release();
      if (write) await write.catch(() => {});
      else writer.release();
    }
  });

  it('resumes after expiry and suppresses a late failure from an older window', async () => {
    const key = endpoint();
    const [first, late, next] = await Promise.all([seedJob(), seedJob(), seedJob()]);
    const opened = await postponeJobForModelQuota(pool, key, first, null);
    expect(opened.consecutiveWindows).toBe(1);
    const paused = await prepareModelEndpoint(pool, key, late);
    expect(paused).toEqual({ outcome: 'requeued', retryAt: opened.retryAt, shouldAlert: false });
    await pool.query("UPDATE model_endpoint_cooldowns SET retry_at = NOW() - interval '1 second' WHERE endpoint_key = $1", [key]);
    expect(await claimDelay(key)).toBeNull();

    const stale = await seedJob();
    expect(await postponeJobForModelQuota(pool, key, stale, null)).toEqual({
      outcome: 'requeued', retryAt: null, shouldAlert: false,
    });
    const preparation = await prepareModelEndpoint(pool, key, next);
    expect(preparation.outcome).toBe('ready');
    if (preparation.outcome !== 'ready') throw new Error('Endpoint still cooling down');
    expect(preparation.observedWindowId).toEqual(expect.any(String));
    expect(await postponeJobForModelQuota(pool, key, next, preparation.observedWindowId)).toMatchObject({
      outcome: 'requeued', shouldAlert: true, consecutiveWindows: 2,
    });
    const current = await pool.query('SELECT window_id FROM model_endpoint_cooldowns WHERE endpoint_key=$1', [key]);
    await recordModelRecovery(pool, key, preparation.observedWindowId!);
    expect((await pool.query('SELECT consecutive_windows FROM model_endpoint_cooldowns WHERE endpoint_key=$1', [key])).rows[0].consecutive_windows).toBe(2);
    await recordModelRecovery(pool, key, current.rows[0].window_id);
    expect((await pool.query('SELECT consecutive_windows FROM model_endpoint_cooldowns WHERE endpoint_key=$1', [key])).rows[0].consecutive_windows).toBe(0);
  });

  it('coalesces into a queued customer successor while preserving operator work and P0 coverage', async () => {
    const key = endpoint();
    const running = await seedJob();
    const ticketId = running.ticketId;
    const successor = await seedJob({ ticketId, queued: true });
    const mention = await seedJob({ ticketId, queued: true, mention: true });
    const before = await Promise.all([row(successor), row(mention)]);
    expect(await postponeJobForModelQuota(pool, key, running, null)).toMatchObject({ outcome: 'coalesced' });
    expect(await row(running)).toMatchObject({ status: 'cancelled', attempts: 2,
      dead_letter_reason: 'coalesced_into_model_quota_successor' });
    expect(await Promise.all([row(successor), row(mention)])).toEqual(before);
    const { rows } = await pool.query('SELECT latest_customer_seq, answered_customer_seq FROM ticket_states WHERE ticket_id = $1', [ticketId]);
    expect(rows[0]).toEqual({ latest_customer_seq: '4', answered_customer_seq: '0' });
  });

  it('returns an operator job to its own queue slot even when a customer successor exists', async () => {
    const key = endpoint();
    const running = await seedJob({ mention: true });
    const successor = await seedJob({ ticketId: running.ticketId, queued: true });
    await pool.query('UPDATE ticket_states SET human_cancelled_at = NOW() WHERE ticket_id = $1', [running.ticketId]);
    const original = await row(running);
    const successorBefore = await row(successor);
    expect(await postponeJobForModelQuota(pool, key, running, null)).toMatchObject({ outcome: 'requeued' });
    expect(await row(running)).toMatchObject({ status: 'queued', attempts: 2, is_operator_mention: true,
      event_id: original.event_id, expected_event_id: original.expected_event_id, session_id: original.session_id,
      available_at: original.available_at });
    expect(await row(successor)).toEqual(successorBefore);
  });

  it('keeps pending customer work when there is only an operator successor', async () => {
    const key = endpoint();
    const running = await seedJob();
    const mention = await seedJob({ ticketId: running.ticketId, queued: true, mention: true });
    const mentionBefore = await row(mention);
    expect(await postponeJobForModelQuota(pool, key, running, null)).toMatchObject({ outcome: 'requeued' });
    expect(await row(running)).toMatchObject({ status: 'queued', attempts: 2, is_operator_mention: false });
    expect(await row(mention)).toEqual(mentionBefore);
  });

  it('rejects an old owner and does not resurrect a human-cancelled customer job', async () => {
    const key = endpoint();
    const running = await seedJob();
    const oldLease = { ...running, leaseOwner: randomUUID() };
    expect(await postponeJobForModelQuota(pool, key, oldLease, null)).toMatchObject({
      outcome: 'lease_lost', shouldAlert: false,
    });
    expect(await claimDelay(key)).toBeNull();
    await pool.query('UPDATE ticket_states SET human_cancelled_at = NOW() WHERE ticket_id = $1', [running.ticketId]);
    expect(await postponeJobForModelQuota(pool, key, running, null)).toMatchObject({ outcome: 'cancelled' });
    expect(await row(running)).toMatchObject({ status: 'cancelled', dead_letter_reason: 'human_takeover' });
  });

  it('preserves a successor that wins the partial unique index during the requeue', async () => {
    const key = endpoint();
    const running = await seedJob();
    const successor = await seedJob({ ticketId: running.ticketId, queued: false });
    const client = await pool.connect();
    const query = client.query.bind(client);
    const runQuery = vi.spyOn(client, 'query');
    let promoted = false;
    runQuery.mockImplementation(async (sql: string, params?: unknown[]) => {
      if (sql.includes("SET status = 'queued'") && !promoted) {
        promoted = true;
        // Mimic an interception writer that does not lock ticket_states.
        await pool.query("UPDATE case_jobs SET status = 'queued' WHERE job_id = $1", [successor.jobId]);
      }
      return query(sql, params);
    });
    try {
      expect(await postponeJobForModelQuota({ connect: async () => client }, key, running, null)).toMatchObject({
        outcome: 'coalesced', shouldAlert: true,
      });
      expect(promoted).toBe(true);
    } finally {
      runQuery.mockRestore();
    }
    expect(await row(successor)).toMatchObject({ status: 'queued', expected_event_id: `cursor-${successor.jobId}` });
  });

  it('rolls the cooldown back if the job transition fails', async () => {
    const key = endpoint();
    const running = await seedJob();
    const client = await pool.connect();
    const query = client.query.bind(client);
    const runQuery = vi.spyOn(client, 'query');
    runQuery.mockImplementation(async (sql: string, params?: unknown[]) => {
      if (sql.includes("SET status = 'queued'")) return query('SELECT 1 / 0');
      return query(sql, params);
    });
    try {
      await expect(postponeJobForModelQuota({ connect: async () => client }, key, running, null))
        .rejects.toThrow('division by zero');
    } finally {
      runQuery.mockRestore();
    }
    expect(await claimDelay(key)).toBeNull();
    expect(await row(running)).toMatchObject({ status: 'running', attempts: 3 });
  });
});
