import { randomUUID } from 'node:crypto';
import type pg from 'pg';
import type { ReadCustomerMessage } from './ticket-context.js';
import type { RelaySendResult } from './relay.js';

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

interface TicketJob {
  job_id: string;
  ticket_id: number;
}

const MAX_ATTEMPTS = 3;
const ACTIVE_STATUSES = "('claimed', 'sandbox_creating', 'running', 'finalizing')";
const UNRESOLVED_DELIVERY = "('pending', 'uncertain')";
const NO_UNRESOLVED_DELIVERY = `NOT EXISTS (
  SELECT 1 FROM public_deliveries AS delivery
  WHERE delivery.ticket_id = job.ticket_id AND delivery.status IN ${UNRESOLVED_DELIVERY}
    AND (delivery.job_id = job.job_id OR NOT job.is_operator_mention)
)`;

export async function claimNextJob(
  pool: pg.Pool, workerId: string, claimDelay?: (client: pg.PoolClient) => Promise<Date | null>,
): Promise<any | null> {
  const client = await pool.connect();
  try {
    await client.query(`
      UPDATE case_jobs
      SET status = 'failed', dead_letter_reason = 'lease_expired_max_attempts_exceeded',
          finished_at = NOW(), updated_at = NOW(), lease_owner = NULL, lease_expires_at = NULL
      WHERE job_id IN (
        SELECT job_id FROM case_jobs AS job
        WHERE status = 'running' AND lease_expires_at <= NOW() AND attempts >= $1
          AND ${NO_UNRESOLVED_DELIVERY}
        FOR UPDATE SKIP LOCKED
      )
    `, [MAX_ATTEMPTS]);
    await client.query('BEGIN');
    if (claimDelay && await claimDelay(client)) {
      await client.query('COMMIT');
      return null;
    }

    // The ticket row is also the ingress, requeue and delivery lock. A queued
    // successor cannot start while any job for this ticket is still active.
    const selected = await client.query(`
      SELECT job_id, job.ticket_id, session_id, event_id, expected_event_id, job.generation,
             is_operator_mention, status AS prev_status, attempts
      FROM case_jobs AS job
      JOIN ticket_states AS state ON state.ticket_id = job.ticket_id AND state.generation = job.generation
      WHERE ${NO_UNRESOLVED_DELIVERY} AND (
        (status = 'running' AND lease_expires_at <= NOW() AND attempts < $1)
        OR (status = 'queued' AND available_at <= NOW() AND NOT EXISTS (
          SELECT 1 FROM case_jobs AS active
          WHERE active.ticket_id = job.ticket_id AND active.status IN ${ACTIVE_STATUSES}
        ))
      )
      ORDER BY (CASE WHEN status = 'running' THEN 0 ELSE 1 END), available_at, created_at, job_id
      LIMIT 1 FOR UPDATE OF state SKIP LOCKED
    `, [MAX_ATTEMPTS]);
    const job = selected.rows[0];
    if (!job) {
      await client.query('COMMIT');
      return null;
    }
    job.ticket_id = ticketIdFromDatabase(job.ticket_id);

    // Recheck with a fresh statement snapshot after acquiring the ticket lock.
    const claimed = await client.query(`
      UPDATE case_jobs AS job
      SET status = 'running', lease_owner = $1, lease_expires_at = NOW() + interval '180 seconds',
          attempts = attempts + 1, updated_at = NOW()
      WHERE job_id = $2 AND ${NO_UNRESOLVED_DELIVERY} AND (
        (status = 'running' AND lease_expires_at <= NOW() AND attempts < $3)
        OR (status = 'queued' AND available_at <= NOW() AND NOT EXISTS (
          SELECT 1 FROM case_jobs AS active
          WHERE active.ticket_id = job.ticket_id AND active.status IN ${ACTIVE_STATUSES}
        ))
      )
      RETURNING attempts
    `, [workerId, job.job_id, MAX_ATTEMPTS]);
    await client.query('COMMIT');
    if (!claimed.rows[0]) return null;
    job.attempts = claimed.rows[0].attempts;
    job.lease_owner = workerId;
    if (job.prev_status === 'running') {
      console.warn(`[Worker] Reclaimed expired job ${job.job_id} for ticket #${job.ticket_id} (attempt ${job.attempts}/${MAX_ATTEMPTS}).`);
    }
    return job;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

interface CustomerCoverage {
  latestSeq: bigint;
  pending: Array<{ seq: bigint; cursor: string | null }>;
  pendingCursors: Set<string>;
  coveredCursors: Set<string>;
}

export async function loadCustomerCoverage(pool: Pick<pg.Pool, 'query'>, ticketId: number): Promise<CustomerCoverage> {
  const result = await pool.query(`
    SELECT state.latest_customer_seq, event.customer_seq, event.message_id, event.answered_by_job_id, event.answered_by_staff
    FROM ticket_states AS state LEFT JOIN events AS event
      ON event.ticket_id = state.ticket_id
      AND (event.customer_seq IS NOT NULL OR event.answered_by_job_id IS NOT NULL)
    WHERE state.ticket_id = $1
  `, [ticketId]);
  const pending: CustomerCoverage['pending'] = [];
  const pendingCursors = new Set<string>();
  const coveredCursors = new Set<string>();
  for (const row of result.rows) {
    if (row.answered_by_job_id != null || row.answered_by_staff) {
      if (row.message_id) coveredCursors.add(row.message_id);
    } else if (row.customer_seq != null) {
      pending.push({ seq: BigInt(row.customer_seq), cursor: row.message_id });
      if (row.message_id) pendingCursors.add(row.message_id);
    }
  }
  return { latestSeq: BigInt(result.rows[0]?.latest_customer_seq ?? 0), pending, pendingCursors, coveredCursors };
}

/** Publish the highest consecutively covered sequence and its actual answering job (null for staff). */
async function refreshTicketCoverage(client: pg.PoolClient, ticketId: number) {
  await client.query(`
    WITH frontier AS (
      SELECT COALESCE((SELECT MIN(customer_seq) - 1 FROM events WHERE ticket_id = $1
        AND customer_seq IS NOT NULL AND answered_by_job_id IS NULL AND NOT answered_by_staff), latest_customer_seq) AS seq
      FROM ticket_states WHERE ticket_id = $1
    )
    UPDATE ticket_states SET answered_customer_seq = frontier.seq,
      answered_by_job_id = (SELECT answered_by_job_id FROM events WHERE ticket_id = $1 AND customer_seq = frontier.seq),
      updated_at = NOW() FROM frontier WHERE ticket_id = $1
  `, [ticketId]);
}

export async function recordStaffCoverage(pool: pg.Pool, ticketId: number, cursors: string[]): Promise<void> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await lockTicket(client, ticketId);
    const updated = await client.query(`
      UPDATE events SET answered_by_staff = TRUE WHERE ticket_id = $1 AND customer_seq IS NOT NULL
        AND message_id = ANY($2::text[]) AND answered_by_job_id IS NULL AND NOT answered_by_staff
    `, [ticketId, cursors]);
    if (updated.rowCount) await refreshTicketCoverage(client, ticketId);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}

async function lockTicket(client: pg.PoolClient, ticketId: number) {
  const result = await client.query(`
    SELECT generation, human_cancelled_at FROM ticket_states WHERE ticket_id = $1 FOR UPDATE
  `, [ticketId]);
  return result.rows[0];
}

/** Retry the same leased job while a known message has yet to appear in history. */
export async function deferUnconfirmedConversation(pool: pg.Pool, job: TicketJob, workerId: string, attempt: number,
  reason = 'waiting_for_conversation_snapshot',
): Promise<boolean> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const ticket = await lockTicket(client, job.ticket_id);
    const result = ticket && !ticket.human_cancelled_at ? await client.query(`
      UPDATE case_jobs SET status = 'queued',
          available_at = NOW() + CASE WHEN created_at < NOW() - interval '1 minute'
                                      THEN interval '5 minutes' ELSE interval '10 seconds' END,
          attempts = GREATEST(attempts - 1, 0), dead_letter_reason = $6,
          snapshot_wait_since = CASE WHEN $6 <> 'waiting_for_conversation_snapshot' THEN NULL
            WHEN dead_letter_reason = $6 THEN COALESCE(snapshot_wait_since, NOW()) ELSE NOW() END,
          snapshot_alert_after = CASE WHEN dead_letter_reason = $6 THEN snapshot_alert_after END,
          updated_at = NOW(), lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL
      WHERE job_id = $1 AND status = 'running' AND lease_owner = $2 AND attempts = $3
        AND generation = $4 AND NOT EXISTS (
          SELECT 1 FROM case_jobs AS queued
          WHERE queued.ticket_id = $5 AND queued.status = 'queued' AND NOT queued.is_operator_mention
        )
    `, [job.job_id, workerId, attempt, ticket.generation, job.ticket_id, reason]) : null;
    await client.query('COMMIT');
    return (result?.rowCount ?? 0) > 0;
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}

export interface RequeueResult {
  outcome: 'created' | 'coalesced' | 'superseded';
  jobId: string;
}

/** Preserve the local event FK and serialize with webhook admission and takeover. */
export async function requeueLatestConversation(
  pool: pg.Pool,
  opts: {
    job: TicketJob;
    latestZendeskEventId?: string;
    observedCustomers?: ReadCustomerMessage[];
    reason: string;
    debounceSeconds?: number;
    lease?: { workerId: string; attempt: number };
  },
): Promise<RequeueResult> {
  const debounce = `${opts.debounceSeconds ?? 10} seconds`;
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const ticket = await lockTicket(client, opts.job.ticket_id);
    const result = await client.query(`
      SELECT generation, is_operator_mention, status, lease_owner, attempts FROM case_jobs
      WHERE job_id = $1 AND ticket_id = $2 FOR UPDATE
    `, [opts.job.job_id, opts.job.ticket_id]);
    const source = result.rows[0];
    if (!source) throw new Error(`Intercepted job ${opts.job.job_id} not found; cannot requeue ticket #${opts.job.ticket_id}`);
    if (!ticket || ticket.human_cancelled_at || source.generation !== ticket.generation || source.is_operator_mention ||
        source.status !== 'running' || (opts.lease &&
          (source.lease_owner !== opts.lease.workerId || source.attempts !== opts.lease.attempt))) {
      await client.query('COMMIT');
      return { outcome: 'superseded', jobId: opts.job.job_id };
    }

    const requeueEventId = `requeue:${randomUUID()}`;
    const observed = opts.observedCustomers ?? [];
    const cursors = observed.at(-1)?.cursors ?? [];
    for (const message of observed) {
      if (message.cursors.length) {
        await recordObservedCustomer(client, opts.job.ticket_id, `observed:${randomUUID()}`, message);
      }
    }
    const coalesced = await client.query(`
      UPDATE case_jobs
      SET available_at = GREATEST(available_at, NOW() + $2::interval), updated_at = NOW()
      WHERE ticket_id = $1 AND generation = $3 AND status = 'queued' AND NOT is_operator_mention
      RETURNING job_id
    `, [opts.job.ticket_id, debounce, ticket.generation]);
    if (coalesced.rows.length > 0) {
      await client.query('COMMIT');
      return { outcome: 'coalesced', jobId: coalesced.rows[0].job_id };
    }

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

    const newJobId = randomUUID();
    await client.query(`
      INSERT INTO case_jobs (
        job_id, session_id, event_id, expected_event_id, ticket_id, generation,
        status, attempts, available_at, created_at, updated_at,
        config_revision, root_model_config_id, child_model_config_ids,
        model_digest, skill_digest, tool_digest
      )
      SELECT $1, $2, $3, $4, ticket_id, generation,
        'queued', 0, NOW() + $6::interval, NOW(), NOW(),
        config_revision, root_model_config_id, child_model_config_ids, model_digest, skill_digest, tool_digest
      FROM case_jobs WHERE job_id = $5
    `, [newJobId, `session-${newJobId}`, requeueEventId,
      cursors[0] || opts.latestZendeskEventId?.trim() || requeueEventId, opts.job.job_id, debounce]);
    await client.query('COMMIT');
    return { outcome: 'created', jobId: newJobId };
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

/** Record a substantive message that appeared in Zendesk before its webhook. Ticket lock required. */
async function recordObservedCustomer(client: pg.PoolClient, ticketId: number, eventId: string, message: ReadCustomerMessage) {
  const time = message.occurredAt && Number.isFinite(Date.parse(message.occurredAt)) ? message.occurredAt : null;
  await client.query(`
    WITH next_sequence AS (
      UPDATE ticket_states SET latest_customer_seq = latest_customer_seq + 1,
          latest_customer_at = GREATEST(latest_customer_at, COALESCE($4::timestamptz, NOW())), updated_at = NOW()
      WHERE ticket_id = $2 AND NOT EXISTS (
        SELECT 1 FROM events WHERE ticket_id = $2 AND message_id = ANY($3::text[])
          AND (customer_seq IS NOT NULL OR answered_by_job_id IS NOT NULL)
      )
      RETURNING latest_customer_seq
    )
    INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, message_id, customer_seq, payload)
    SELECT $1, $2, COALESCE($4::timestamptz, NOW()), 'pi-agent', 'customer', 'customer.observed',
      ($3::text[])[1], latest_customer_seq, '{}'::json FROM next_sequence
  `, [eventId, ticketId, message.cursors, time]);
}

type DeliveryOptions = {
  job: TicketJob; workerId: string; attempt: number; startingSeq: bigint;
  messages: ReadCustomerMessage[]; reply: string; signal?: AbortSignal;
};

export type PublicDeliveryReceipt =
  | { source: 'browser-relay'; total_duration_ms?: number; timings?: Record<string, number> }
  | { source: 'zendesk-conversation'; message_id: string };

export interface PublicDeliveryCheck {
  job_id: string;
  delivery_id: string;
  ticket_id: number;
  started_at: Date;
  last_error: string | null;
  alert_sent_at: Date | null;
}

type DeliveryFence = 'superseded' | 'new_customer_message' | 'already_covered' | 'unidentified_messages';
type PublicDeliveryResult =
  | { outcome: DeliveryFence }
  | { outcome: 'already_sent' | 'blocked'; deliveryId: string }
  | { outcome: 'uncertain'; deliveryId: string; reason: string; jobStatus?: string }
  | { outcome: 'delivered'; deliveryId: string; delivery: PublicDeliveryReceipt; jobStatus: string };

async function deliveryTransaction<T>(pool: pg.Pool, ticketId: number,
  action: (client: pg.PoolClient, ticket: { generation: string; human_cancelled_at: Date | null } | undefined) => Promise<T>,
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const ticket = await lockTicket(client, ticketId);
    const result = await action(client, ticket);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}

async function checkPublicDeliveryFence(client: pg.PoolClient, opts: DeliveryOptions,
  ticket: { generation: string; human_cancelled_at: Date | null } | undefined,
): Promise<DeliveryFence | null> {
  if (!ticket || ticket.human_cancelled_at || opts.signal?.aborted) return 'superseded';
  const leased = await client.query(`
    SELECT 1 FROM case_jobs WHERE job_id = $1 AND ticket_id = $2 AND generation = $3
      AND status = 'running' AND NOT is_operator_mention AND lease_owner = $4 AND attempts = $5
      AND lease_expires_at > clock_timestamp() FOR UPDATE
  `, [opts.job.job_id, opts.job.ticket_id, ticket.generation, opts.workerId, opts.attempt]);
  if (!leased.rowCount) return 'superseded';
  if (!opts.messages.length || opts.messages.some(message => !message.cursors.length)) return 'unidentified_messages';
  const cursors = [...new Set(opts.messages.flatMap(message => message.cursors))];
  const events = await client.query(`
    SELECT
      EXISTS (SELECT 1 FROM events WHERE ticket_id = $1 AND customer_seq > $2
        AND answered_by_job_id IS NULL AND NOT answered_by_staff
        AND (message_id IS NULL OR message_id <> ALL($3::text[]))) AS newer,
      EXISTS (SELECT 1 FROM events WHERE ticket_id = $1 AND message_id = ANY($3::text[])
        AND (answered_by_job_id IS NOT NULL OR answered_by_staff)) AS covered
  `, [opts.job.ticket_id, String(opts.startingSeq), cursors]);
  if (events.rows[0].newer) return 'new_customer_message';
  return events.rows[0].covered ? 'already_covered' : null;
}

/** The final permit is committed before invoking the relay. No transaction spans external I/O. */
export async function deliverWithCustomerCoverage(
  pool: pg.Pool, opts: DeliveryOptions, deliver: (deliveryId: string, reply: string) => Promise<RelaySendResult>,
): Promise<PublicDeliveryResult> {
  const { job } = opts;
  const messages = JSON.stringify(opts.messages);
  const reply = opts.reply;
  if (!reply.trim()) throw new Error('Cannot reserve an empty public reply');
  const reservation = await deliveryTransaction(pool, job.ticket_id, async (client, ticket) => {
    const fence = await checkPublicDeliveryFence(client, opts, ticket);
    if (fence) return { outcome: fence } as const;
    const existing = await client.query(`
      SELECT delivery_id, status FROM public_deliveries WHERE ticket_id = $1
        AND (status IN ${UNRESOLVED_DELIVERY} OR (job_id = $2 AND status = 'sent'))
      ORDER BY (status = 'sent') DESC LIMIT 1
    `, [job.ticket_id, job.job_id]);
    const prior = existing.rows[0];
    if (prior) return {
      outcome: prior.status === 'sent' ? 'already_sent' as const : 'blocked' as const,
      deliveryId: prior.delivery_id as string,
    };
    const { rows } = await client.query<{ delivery_id: string }>(`
      INSERT INTO public_deliveries (job_id, ticket_id, delivery_id, status, messages, reply_body)
      VALUES ($1, $2, $3, 'reserved', $4::jsonb, $5)
      ON CONFLICT (job_id) DO UPDATE SET status = 'reserved', messages = EXCLUDED.messages,
        reply_body = EXCLUDED.reply_body, last_error = NULL, updated_at = clock_timestamp()
      WHERE public_deliveries.status IN ('reserved', 'abandoned')
      RETURNING delivery_id
    `, [job.job_id, job.ticket_id, randomUUID(), messages, reply]);
    if (!rows[0]) return { outcome: 'superseded' } as const;
    return { outcome: 'reserved', deliveryId: rows[0].delivery_id } as const;
  });
  if (reservation.outcome !== 'reserved') return reservation;
  const { deliveryId } = reservation;

  // Ingress/handoff or another lease may win between reservation and dispatch.
  const fence = await deliveryTransaction(pool, job.ticket_id, async (client, ticket) => {
    const reason = await checkPublicDeliveryFence(client, opts, ticket);
    if (reason) return reason;
    const started = await client.query(`
      UPDATE public_deliveries SET status = 'pending', started_at = clock_timestamp(),
        updated_at = clock_timestamp(), next_check_at = clock_timestamp() + interval '2 minutes'
      WHERE delivery_id = $1 AND status = 'reserved' AND messages = $2::jsonb AND reply_body = $3
    `, [deliveryId, messages, reply]);
    return started.rowCount ? null : 'superseded';
  });
  if (fence) return { outcome: fence };
  if (opts.signal?.aborted) {
    // The current stack can prove this permit never reached the relay.
    await pool.query(`
      UPDATE public_deliveries SET status = 'abandoned', started_at = NULL,
        updated_at = clock_timestamp(), next_check_at = NULL
      WHERE delivery_id = $1 AND status = 'pending'
    `, [deliveryId]);
    return { outcome: 'superseded' };
  }

  const identity = { job_id: job.job_id, ticket_id: job.ticket_id, delivery_id: deliveryId };
  let receipt: PublicDeliveryReceipt | undefined;
  try {
    const response = await deliver(deliveryId, reply);
    if (response.ok !== true || response.status !== 'sent' || response.ticket_id !== job.ticket_id) {
      throw new Error('Browser relay did not confirm this ticket was sent');
    }
    const responseReceipt = { source: 'browser-relay' as const, total_duration_ms: response.total_duration_ms, timings: response.timings };
    receipt = responseReceipt;
    // A lost database response can leave the outcome committed or rolled back.
    // Retry the local transaction once with the same positive evidence.
    const jobStatus = await confirmPublicDelivery(pool, identity, responseReceipt)
      .catch(() => confirmPublicDelivery(pool, identity, responseReceipt));
    return { outcome: 'delivered', deliveryId, delivery: responseReceipt, jobStatus };
  } catch (error) {
    const reason = receipt ? 'public_delivery_receipt_persistence_failed' : 'browser_relay_confirmation_missing';
    console.warn(`[Worker] Public delivery ${deliveryId} for ticket #${job.ticket_id}: ${reason}`,
      error instanceof Error ? error.name : 'unknown error');
    try {
      const current = await markPublicDeliveryUncertain(pool, identity, reason, true);
      if (current?.status === 'sent') {
        return { outcome: 'delivered', deliveryId, delivery: current.receipt, jobStatus: current.job_status };
      }
      if (current?.status === 'uncertain') return { outcome: 'uncertain', deliveryId, reason, jobStatus: current.job_status };
    } catch (persistenceError) {
      console.error(`[Worker] Could not update public delivery ${deliveryId}; the dispatch permit remains fenced`,
        (persistenceError as { code?: string } | null)?.code ?? 'unknown database error');
    }
    return { outcome: 'uncertain', deliveryId, reason };
  }
}

type DeliveryIdentity = Pick<PublicDeliveryCheck, 'delivery_id' | 'ticket_id' | 'job_id'>;

async function storePublicDeliveryOutcome(client: pg.PoolClient, identity: DeliveryIdentity,
  status: 'sent' | 'uncertain', receipt: PublicDeliveryReceipt | null, reason: string | null,
): Promise<string> {
  const jobStatus = status === 'sent' ? 'completed' : 'failed';
  await client.query(`
    UPDATE case_jobs SET status = $2, dead_letter_reason = $3, finished_at = clock_timestamp(),
      updated_at = clock_timestamp(), lease_owner = NULL, lease_expires_at = NULL
    WHERE job_id = $1 AND (status IN ('running', 'queued') OR
      ($2 = 'completed' AND status = 'failed' AND dead_letter_reason = 'public_delivery_uncertain'))
  `, [identity.job_id, jobStatus, status === 'uncertain' ? 'public_delivery_uncertain' : null]);

  const { rows } = await client.query<{ status: string }>(`
    INSERT INTO run_records (session_id, job_id, ticket_id, generation, status, outcome,
      created_at, started_at, finished_at, updated_at)
    SELECT CASE WHEN length(j.job_id) <= 244 THEN 'pi-session-' || j.job_id ELSE j.session_id END,
      j.job_id, j.ticket_id, GREATEST(j.generation, 1),
      CASE WHEN j.status IN ('cancelled', 'human_takeover') THEN j.status ELSE $3 END,
      jsonb_build_object('status', CASE WHEN j.status IN ('cancelled', 'human_takeover') THEN j.status ELSE $3 END,
        'final_reply', d.reply_body, 'error', $4::text, 'delivery', $5::jsonb),
      j.created_at, d.started_at, clock_timestamp(), clock_timestamp()
    FROM case_jobs j JOIN public_deliveries d ON d.job_id = j.job_id
    WHERE j.job_id = $1 AND d.delivery_id = $2
    ON CONFLICT (job_id) DO UPDATE SET status = EXCLUDED.status,
      outcome = run_records.outcome::jsonb || EXCLUDED.outcome::jsonb,
      finished_at = EXCLUDED.finished_at, updated_at = EXCLUDED.updated_at
    RETURNING status
  `, [identity.job_id, identity.delivery_id, jobStatus, reason, JSON.stringify({
    channel: 'browser-relay', status, delivery_id: identity.delivery_id, ...receipt,
  })]);
  return rows[0].status;
}

/** Positive evidence can settle the original intent even after its lease or generation ends. */
export async function confirmPublicDelivery(pool: pg.Pool, identity: DeliveryIdentity, receipt: PublicDeliveryReceipt): Promise<string> {
  return deliveryTransaction(pool, identity.ticket_id, async client => {
    const { rows } = await client.query<{ messages: ReadCustomerMessage[] }>(`
      UPDATE public_deliveries SET status = 'sent', confirmed_at = clock_timestamp(),
        updated_at = clock_timestamp(), receipt = $4::jsonb, last_error = NULL, next_check_at = NULL
      WHERE job_id = $1 AND ticket_id = $2 AND delivery_id = $3 AND status IN ${UNRESOLVED_DELIVERY}
      RETURNING messages
    `, [identity.job_id, identity.ticket_id, identity.delivery_id, JSON.stringify(receipt)]);
    if (!rows[0]) {
      const confirmed = await client.query<{ status: string }>(`
        SELECT run.status FROM public_deliveries AS d JOIN run_records AS run ON run.job_id = d.job_id
        WHERE d.delivery_id = $1 AND d.status = 'sent'
      `, [identity.delivery_id]);
      if (!confirmed.rows[0]) throw new Error('Public delivery is missing an active permit');
      return confirmed.rows[0].status;
    }

    for (const message of rows[0].messages) {
      const cursors = [...new Set(message.cursors)];
      await recordObservedCustomer(client, identity.ticket_id, `observed:${randomUUID()}`, message);
      await client.query(`
        UPDATE events SET answered_by_job_id = $3
        WHERE ticket_id = $1 AND message_id = ANY($2::text[]) AND customer_seq IS NOT NULL
          AND answered_by_job_id IS NULL AND NOT answered_by_staff
      `, [identity.ticket_id, cursors, identity.job_id]);
      // Persist aliases for webhooks that arrive after the confirmed send.
      await client.query(`
        INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, message_id, answered_by_job_id, payload)
        SELECT $4 || ':' || position, $1, NOW(), 'pi-agent', 'customer', 'customer.covered_alias', cursor, $3, '{}'::json
        FROM unnest($2::text[]) WITH ORDINALITY AS aliases(cursor, position)
        WHERE NOT EXISTS (
          SELECT 1 FROM events WHERE ticket_id = $1 AND message_id = cursor AND answered_by_job_id IS NOT NULL
        )
      `, [identity.ticket_id, cursors, identity.job_id, `covered:${randomUUID()}`]);
    }
    await refreshTicketCoverage(client, identity.ticket_id);
    return storePublicDeliveryOutcome(client, identity, 'sent', receipt, null);
  });
}

/** Claim read-only reconciliation independently of model jobs and their lease budget. */
export async function claimPublicDeliveryCheck(pool: pg.Pool): Promise<PublicDeliveryCheck | null> {
  const { rows } = await pool.query<Omit<PublicDeliveryCheck, 'ticket_id'> & { ticket_id: string }>(`
    UPDATE public_deliveries AS d SET next_check_at = clock_timestamp() + interval '1 minute'
    WHERE d.job_id = (
      SELECT job_id FROM public_deliveries WHERE status IN ${UNRESOLVED_DELIVERY}
        AND next_check_at <= clock_timestamp()
      ORDER BY next_check_at, job_id LIMIT 1 FOR UPDATE SKIP LOCKED
    )
    RETURNING job_id, delivery_id, ticket_id, started_at, last_error, alert_sent_at
  `);
  return rows[0] ? { ...rows[0], ticket_id: ticketIdFromDatabase(rows[0].ticket_id) } : null;
}

export async function markPublicDeliveryUncertain(
  pool: pg.Pool, identity: DeliveryIdentity, reason: string, checkImmediately = false,
): Promise<{ status: 'sent'; receipt: PublicDeliveryReceipt; job_status: string } | { status: 'uncertain'; job_status: string } | null> {
  return deliveryTransaction(pool, identity.ticket_id, async client => {
    const { rowCount } = await client.query(`
      UPDATE public_deliveries SET status = 'uncertain', last_error = $4, updated_at = clock_timestamp(),
        next_check_at = CASE WHEN $5 THEN clock_timestamp() ELSE next_check_at END
      WHERE job_id = $1 AND ticket_id = $2 AND delivery_id = $3 AND status = 'pending'
    `, [identity.job_id, identity.ticket_id, identity.delivery_id, reason, checkImmediately]);
    if (rowCount) {
      const jobStatus = await storePublicDeliveryOutcome(client, identity, 'uncertain', null, reason);
      return { status: 'uncertain', job_status: jobStatus };
    }
    const { rows } = await client.query(`
      SELECT d.status, d.receipt, run.status AS job_status FROM public_deliveries AS d
      LEFT JOIN run_records AS run ON run.job_id = d.job_id
      WHERE d.job_id = $1 AND d.ticket_id = $2 AND d.delivery_id = $3 AND d.status IN ('sent', 'uncertain')
    `, [identity.job_id, identity.ticket_id, identity.delivery_id]);
    return rows[0] ?? null;
  });
}

export async function finishPublicDeliveryCheck(pool: pg.Pool, deliveryId: string, alerted: boolean): Promise<void> {
  await pool.query(`
    UPDATE public_deliveries SET next_check_at = clock_timestamp() + CASE
        WHEN started_at < clock_timestamp() - interval '1 day' THEN interval '30 minutes' ELSE interval '5 minutes' END,
      alert_sent_at = CASE WHEN $2 THEN COALESCE(alert_sent_at, clock_timestamp()) ELSE alert_sent_at END,
      updated_at = clock_timestamp()
    WHERE delivery_id = $1 AND status = 'uncertain'
  `, [deliveryId, alerted]);
}

/** A manually requeued confirmed job is restored without reading context or sending again. */
export async function completePreviouslySentPublicJob(pool: pg.Pool, job: TicketJob, workerId: string, attempt: number): Promise<boolean> {
  const result = await pool.query(`
    UPDATE case_jobs AS j SET status = 'completed', finished_at = clock_timestamp(), updated_at = clock_timestamp(),
      dead_letter_reason = NULL, lease_owner = NULL, lease_expires_at = NULL
    WHERE j.job_id = $1 AND j.status = 'running' AND j.lease_owner = $2 AND j.attempts = $3
      AND EXISTS (SELECT 1 FROM public_deliveries AS d WHERE d.job_id = j.job_id AND d.status = 'sent')
  `, [job.job_id, workerId, attempt]);
  return Boolean(result.rowCount);
}
