import { randomUUID } from 'node:crypto';
import type pg from 'pg';

// PostgreSQL BIGINT is returned as a string by node-postgres. Remote Zendesk
// tools require an integer, so normalize once at the queue boundary.
export function ticketIdFromDatabase(value: unknown): number {
  if (typeof value !== 'string' && typeof value !== 'number') {
    throw new Error('Invalid ticket_id returned by the job queue');
  }
  const ticketId = Number(value);
  if (!Number.isSafeInteger(ticketId) || ticketId <= 0 ||
      (typeof value === 'string' && !/^[1-9]\d*$/.test(value))) {
    throw new Error('Invalid ticket_id returned by the job queue');
  }
  return ticketId;
}

export async function claimNextJob(pool: pg.Pool, workerId: string): Promise<any | null> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');

    // 1. Automatically dead-letter any jobs whose lease expired and max attempts (3) have been reached
    await client.query(`
      UPDATE case_jobs
      SET status = 'failed',
          dead_letter_reason = 'lease_expired_max_attempts_exceeded',
          finished_at = NOW(),
          updated_at = NOW(),
          lease_owner = NULL,
          lease_expires_at = NULL
      WHERE status = 'running'
        AND lease_expires_at IS NOT NULL
        AND lease_expires_at <= NOW()
        AND attempts >= 3
    `);

    // 2. Select next job: either standard queued job OR expired running lease with attempts < 3
    const selectRes = await client.query(`
      SELECT job_id, ticket_id, session_id, event_id, expected_event_id, generation, status as prev_status, attempts
      FROM case_jobs
      WHERE (
        (status = 'queued' AND available_at <= NOW())
        OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= NOW() AND attempts < 3)
      )
      ORDER BY (CASE WHEN status = 'running' THEN 0 ELSE 1 END), available_at ASC, created_at ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    `);

    if (selectRes.rows.length === 0) {
      // Persist expired-job cleanup even when there is no next job.
      await client.query('COMMIT');
      return null;
    }

    const job = selectRes.rows[0];
    job.ticket_id = ticketIdFromDatabase(job.ticket_id);
    if (job.prev_status === 'running') {
      console.warn(`[Worker] 🔄 Lease Reclaim: Reclaiming expired running job ${job.job_id} for ticket #${job.ticket_id} (lease expired, retry attempt ${job.attempts + 1}/3)...`);
    }

    await client.query(`
      UPDATE case_jobs
      SET status = 'running',
          lease_owner = $1,
          lease_expires_at = NOW() + interval '180 seconds',
          attempts = attempts + 1,
          updated_at = NOW()
      WHERE job_id = $2
    `, [workerId, job.job_id]);

    await client.query('COMMIT');
    return job;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

export interface RequeueResult {
  outcome: 'created' | 'coalesced';
  jobId: string;
}

/**
 * Queue a successor run after a job was intercepted because the customer wrote
 * again while it was reasoning.
 *
 * case_jobs.event_id must reference a row in the local events table, and it is
 * unique. The latest message id the worker sees comes from the Zendesk ticket
 * audit, not from ingress, and the interception usually fires before ingress
 * has recorded that message, so it cannot be used as the job's event. Instead
 * this records a dedicated internal event for the requeue and points the job at
 * it; the Zendesk id is kept only as the expected_event_id cursor.
 */
export async function requeueLatestConversation(
  pool: pg.Pool,
  opts: {
    job: { job_id: string; ticket_id: number };
    latestZendeskEventId?: string;
    reason: string;
    debounceSeconds?: number;
  },
): Promise<RequeueResult> {
  const debounce = `${opts.debounceSeconds ?? 10} seconds`;
  const client = await pool.connect();
  try {
    await client.query('BEGIN');

    // If ingress (or an earlier interception) already queued a successor for
    // this ticket, only push its start back so it reads the newest messages.
    // Its expected_event_id is left alone: it may be an operator mention.
    const coalesced = await client.query(`
      UPDATE case_jobs
      SET available_at = NOW() + $2::interval,
          updated_at = NOW()
      WHERE ticket_id = $1 AND status = 'queued'
      RETURNING job_id
    `, [opts.job.ticket_id, debounce]);
    if (coalesced.rows.length > 0) {
      await client.query('COMMIT');
      return { outcome: 'coalesced', jobId: coalesced.rows[0].job_id };
    }

    const requeueEventId = `requeue:${opts.job.job_id}:${randomUUID()}`;
    await client.query(`
      INSERT INTO events (
        event_id, ticket_id, occurred_at, source, customer_origin, event_type, payload, received_at
      )
      VALUES ($1, $2, NOW(), 'pi-agent', 'internal', 'requeue_after_interception', $3::json, NOW())
      ON CONFLICT (event_id) DO NOTHING
    `, [requeueEventId, opts.job.ticket_id, JSON.stringify({
      intercepted_job_id: opts.job.job_id,
      latest_zendesk_event_id: opts.latestZendeskEventId ?? null,
      reason: opts.reason,
    })]);

    // Inherit generation and the frozen config of the intercepted job. A
    // successor queued concurrently by ingress wins the one-queued-per-ticket
    // index and is reused.
    const newJobId = randomUUID();
    const inserted = await client.query(`
      INSERT INTO case_jobs (
        job_id, session_id, event_id, expected_event_id, ticket_id, generation,
        status, attempts, available_at, created_at, updated_at,
        config_revision, root_model_config_id, child_model_config_ids,
        model_digest, skill_digest, tool_digest
      )
      SELECT
        $1, $2, $3, $4, ticket_id, generation,
        'queued', 0, NOW() + $6::interval, NOW(), NOW(),
        config_revision, root_model_config_id, child_model_config_ids,
        model_digest, skill_digest, tool_digest
      FROM case_jobs
      WHERE job_id = $5
      ON CONFLICT (ticket_id) WHERE status = 'queued'
      DO UPDATE SET
        available_at = EXCLUDED.available_at,
        updated_at = NOW()
      RETURNING job_id
    `, [
      newJobId,
      `session-${newJobId}`,
      requeueEventId,
      opts.latestZendeskEventId?.trim() || requeueEventId,
      opts.job.job_id,
      debounce,
    ]);
    if (inserted.rows.length === 0) {
      throw new Error(`Intercepted job ${opts.job.job_id} not found; cannot requeue ticket #${opts.job.ticket_id}`);
    }

    await client.query('COMMIT');
    const jobId = inserted.rows[0].job_id;
    return { outcome: jobId === newJobId ? 'created' : 'coalesced', jobId };
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}
