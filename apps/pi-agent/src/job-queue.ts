import type pg from 'pg';

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

