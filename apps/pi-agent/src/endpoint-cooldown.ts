import { createHash, randomUUID } from 'node:crypto';
import type pg from 'pg';

const COOLDOWN_SECONDS = 5 * 60;
const ENDPOINT_LOCK_KEY = "hashtextextended('pi-agent:model-quota:' || $1::text, 0)";

type Client = Pick<pg.PoolClient, 'query'>;
type Pool = Pick<pg.Pool, 'connect'>;

export interface RunningJobLease {
  jobId: string;
  ticketId: number;
  /** Unique owner token for this claim, also used by its heartbeat. */
  leaseOwner: string;
}

interface CooldownWindow {
  window_id: string;
  retry_at: Date;
  cooling_down: boolean;
}

export interface ModelQuotaPostponement {
  outcome: 'requeued' | 'coalesced' | 'cancelled' | 'lease_lost';
  retryAt: Date | null;
  consecutiveWindows?: number;
  /** Send the endpoint alert only after this transaction has committed. */
  shouldAlert: boolean;
}

type ModelEndpointPreparation = { outcome: 'ready'; observedWindowId: string | null }
  | ModelQuotaPostponement;

/** Match host and base path across spelling variants without storing connection details. */
export function modelEndpointKey(baseUrl: string): string {
  const configured = baseUrl.trim();
  const url = URL.parse(configured);
  const base = (url ? `${url.origin}${url.pathname}` : configured).replace(/\/+$/, '');
  // The model runtime appends /v1 when it is omitted from the configured base.
  const endpoint = base.endsWith('/v1') ? base : `${base}/v1`;
  return createHash('sha256').update(endpoint).digest('hex');
}

/** Call only for failures from the model invocation. The SDK often flattens HTTP errors to text. */
export function isModelQuotaError(error: unknown): boolean {
  const details = error && typeof error === 'object'
    ? error as { status?: unknown; statusCode?: unknown; response?: { status?: unknown }; message?: unknown }
    : undefined;
  const status = details?.status ?? details?.statusCode ?? details?.response?.status;
  if (status !== undefined) return status === 402 || status === '402';
  const message = details?.message ?? error;
  if (typeof message !== 'string') return false;
  const providerMessage = message.trim().replace(/^Pi Agent model request failed for ticket #\d+:\s*/i, '');
  return /^(?:(?:HTTP\s+)?402(?:\s|:|$)|OpenAI API error\s*\(402\)(?:\s|:|$))/i.test(providerMessage);
}

async function readWindow(client: Client, endpointKey: string): Promise<CooldownWindow | undefined> {
  const { rows } = await client.query<CooldownWindow>(`
    SELECT window_id, retry_at, retry_at > clock_timestamp() AS cooling_down
    FROM model_endpoint_cooldowns
    WHERE endpoint_key = $1
  `, [endpointKey]);
  return rows[0];
}

/** Call inside the READ COMMITTED claim transaction, before selecting/updating jobs. */
export async function getEndpointClaimDelay(client: Client, endpointKey: string): Promise<Date | null> {
  // The shared lock also protects the absence of a row on a healthy endpoint.
  await client.query(`SELECT pg_advisory_xact_lock_shared(${ENDPOINT_LOCK_KEY})`, [endpointKey]);
  const window = await readWindow(client, endpointKey);
  return window?.cooling_down ? window.retry_at : null;
}

export async function activeModelClaimDelay(client: Client): Promise<Date | null> {
  // A fresh deployment can receive jobs before Admin has saved its first model.
  const table = await client.query("SELECT to_regclass('system_settings') AS settings");
  if (!table.rows[0]?.settings) return null;
  const config = await client.query("SELECT value->>'base_url' AS endpoint FROM system_settings WHERE key = 'model_config'");
  const endpoint = config.rows[0]?.endpoint;
  return typeof endpoint === 'string' && endpoint.trim() ? getEndpointClaimDelay(client, modelEndpointKey(endpoint)) : null;
}

async function transaction<T>(pool: Pool, work: (client: Client) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN ISOLATION LEVEL READ COMMITTED');
    const result = await work(client);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}

async function lockRunningJob(client: Client, lease: RunningJobLease) {
  // Ingress and human handoff acquire the ticket generation fence before touching jobs.
  await client.query(`
    INSERT INTO ticket_states (ticket_id) VALUES ($1) ON CONFLICT (ticket_id) DO NOTHING
  `, [lease.ticketId]);
  const state = await client.query<{ generation: string; human_cancelled: boolean }>(`
    SELECT generation::text, human_cancelled_at IS NOT NULL AS human_cancelled
    FROM ticket_states WHERE ticket_id = $1 FOR UPDATE
  `, [lease.ticketId]);
  const owned = await client.query<{ generation: string; operator_mention: boolean }>(`
    SELECT generation::text, is_operator_mention AS operator_mention
    FROM case_jobs
    WHERE job_id = $1 AND ticket_id = $2 AND status = 'running'
      AND lease_owner = $3 AND lease_expires_at > clock_timestamp()
    FOR UPDATE
  `, [lease.jobId, lease.ticketId, lease.leaseOwner]);
  const job = owned.rows[0];
  if (!job) return null;
  const ticket = state.rows[0];
  const cancellation = job.generation !== ticket.generation ? 'stale_ticket_generation'
    : ticket.human_cancelled && !job.operator_mention ? 'human_takeover' : null;
  return { generation: job.generation, operatorMention: job.operator_mention, cancellation };
}

async function settleRunningJob(
  client: Client,
  lease: RunningJobLease,
  job: NonNullable<Awaited<ReturnType<typeof lockRunningJob>>>,
): Promise<'requeued' | 'coalesced' | 'cancelled'> {
  let cancellation = job.cancellation;
  let successorFound = false;
  while (!cancellation) {
    // P0 gives operator requests independent queue slots. A paused mention
    // keeps its own event and instructions, and only customer work coalesces.
    if (!job.operatorMention) {
      const successor = await client.query<{ job_id: string; generation: string }>(`
        SELECT job_id, generation::text
        FROM case_jobs WHERE ticket_id = $1 AND status = 'queued' AND NOT is_operator_mention
        FOR UPDATE
      `, [lease.ticketId]);
      const queued = successor.rows[0];
      if (queued?.generation === job.generation) {
        successorFound = true;
        cancellation = 'coalesced_into_model_quota_successor';
        break;
      }
      if (queued) {
        // A stale generation cannot stand in for this run or occupy its queue slot.
        await client.query(`
          UPDATE case_jobs SET status = 'cancelled', dead_letter_reason = 'stale_ticket_generation',
              finished_at = clock_timestamp(), updated_at = clock_timestamp()
          WHERE job_id = $1
        `, [queued.job_id]);
      }
    }
    // The legacy interception path can insert a successor without the ticket lock.
    // Recover the unique-index race and reconsider that successor under its row lock.
    await client.query('SAVEPOINT model_quota_requeue');
    try {
      await client.query(`
        UPDATE case_jobs SET status = 'queued', attempts = GREATEST(attempts - 1, 0),
            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
            finished_at = NULL, dead_letter_reason = NULL, updated_at = clock_timestamp()
        WHERE job_id = $1
      `, [lease.jobId]);
      await client.query('RELEASE SAVEPOINT model_quota_requeue');
      return 'requeued';
    } catch (error) {
      const conflict = error as pg.DatabaseError;
      if (conflict.code !== '23505' || conflict.constraint !== 'uq_case_jobs_one_queued_ticket') throw error;
      await client.query('ROLLBACK TO SAVEPOINT model_quota_requeue');
      await client.query('RELEASE SAVEPOINT model_quota_requeue');
    }
  }
  await client.query(`
    UPDATE case_jobs SET status = 'cancelled', attempts = GREATEST(attempts - 1, 0),
        lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
        finished_at = clock_timestamp(), dead_letter_reason = $2, updated_at = clock_timestamp()
    WHERE job_id = $1
  `, [lease.jobId, cancellation]);
  return successorFound ? 'coalesced' : 'cancelled';
}

/** Check again after loading the active config and before invoking its model. */
export async function prepareModelEndpoint(
  pool: Pool, endpointKey: string, lease: RunningJobLease,
): Promise<ModelEndpointPreparation> {
  return transaction(pool, async client => {
    await client.query(`SELECT pg_advisory_xact_lock_shared(${ENDPOINT_LOCK_KEY})`, [endpointKey]);
    const window = await readWindow(client, endpointKey);
    if (!window?.cooling_down) return { outcome: 'ready', observedWindowId: window?.window_id ?? null };
    const job = await lockRunningJob(client, lease);
    const outcome = job ? await settleRunningJob(client, lease, job) : 'lease_lost';
    return { outcome, retryAt: window.retry_at, shouldAlert: false };
  });
}

/** Open one finite cooldown window and postpone the owned job in the same transaction. */
export async function postponeJobForModelQuota(
  pool: Pool, endpointKey: string, lease: RunningJobLease, observedWindowId: string | null,
): Promise<ModelQuotaPostponement> {
  return transaction(pool, async client => {
    await client.query(`SELECT pg_advisory_xact_lock(${ENDPOINT_LOCK_KEY})`, [endpointKey]);
    const job = await lockRunningJob(client, lease);
    if (!job) return { outcome: 'lease_lost', retryAt: null, shouldAlert: false };

    const window = await readWindow(client, endpointKey);
    let retryAt = window?.cooling_down ? window.retry_at : null;
    let shouldAlert = false;
    let consecutiveWindows: number | undefined;
    // An in-flight request that missed a window (even one that has expired)
    // cannot open the next window. Only attempts admitted since it opened can.
    if (!retryAt && (window?.window_id ?? null) === observedWindowId) {
      const { rows } = await client.query<{ retry_at: Date; consecutive_windows: number }>(`
        INSERT INTO model_endpoint_cooldowns (endpoint_key, window_id, retry_at)
        VALUES ($1, $2, clock_timestamp() + make_interval(secs => $3))
        ON CONFLICT (endpoint_key) DO UPDATE
          SET window_id = EXCLUDED.window_id, retry_at = EXCLUDED.retry_at,
              consecutive_windows = model_endpoint_cooldowns.consecutive_windows + 1
        RETURNING retry_at, consecutive_windows
      `, [endpointKey, randomUUID(), COOLDOWN_SECONDS]);
      retryAt = rows[0].retry_at;
      consecutiveWindows = rows[0].consecutive_windows;
      shouldAlert = true;
    }
    const outcome = await settleRunningJob(client, lease, job);
    return { outcome, retryAt, shouldAlert, ...(shouldAlert ? { consecutiveWindows } : {}) };
  });
}

/** A successful request from a previous window cannot clear a newer outage. */
export async function recordModelRecovery(client: Client, endpointKey: string, windowId: string): Promise<void> {
  await client.query(`
    UPDATE model_endpoint_cooldowns SET consecutive_windows = 0
    WHERE endpoint_key = $1 AND window_id = $2 AND consecutive_windows > 0
  `, [endpointKey, windowId]);
}
