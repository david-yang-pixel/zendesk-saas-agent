import type pg from 'pg';
import { sendFeishuAlert } from './alerts.js';

type Query = Pick<pg.Pool, 'query'>;

/** Age of the oldest still-unanswered customer message for a runnable ticket. */
export async function oldestWaitingTicket(db: Query): Promise<{ ticket_id: string; minutes: number } | undefined> {
  const result = await db.query<{ ticket_id: string; minutes: number }>(`
    SELECT j.ticket_id, GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (NOW() -
      COALESCE(MIN(e.received_at), MIN(j.created_at)))) / 60))::integer AS minutes
    FROM case_jobs j
    JOIN ticket_states s ON s.ticket_id = j.ticket_id AND s.generation = j.generation
    LEFT JOIN events e ON e.ticket_id = j.ticket_id AND e.customer_seq IS NOT NULL
      AND e.answered_by_job_id IS NULL AND NOT e.answered_by_staff
    WHERE j.status IN ('queued', 'running') AND NOT j.is_operator_mention
      AND s.human_cancelled_at IS NULL AND s.latest_customer_seq > s.answered_customer_seq
    GROUP BY j.ticket_id ORDER BY minutes DESC, j.ticket_id LIMIT 1
  `);
  return result.rows[0];
}

/** Reserve a single reminder in the database so concurrent workers and restarts share its cadence. */
export async function notifySnapshotWaits(db: Query, notify: typeof sendFeishuAlert, webhook: string) {
  const { rows } = await db.query<{ job_id: string; ticket_id: string; minutes: number; reservation: string }>(`
    WITH due AS (
      SELECT j.job_id FROM case_jobs j JOIN ticket_states s ON s.ticket_id = j.ticket_id
      WHERE j.status = 'queued' AND j.generation = s.generation AND s.human_cancelled_at IS NULL
        AND j.dead_letter_reason = 'waiting_for_conversation_snapshot'
        AND j.snapshot_wait_since <= NOW() - interval '15 minutes'
        AND (j.snapshot_alert_after IS NULL OR j.snapshot_alert_after <= NOW())
      ORDER BY j.snapshot_wait_since LIMIT 1 FOR UPDATE OF j SKIP LOCKED
    ) UPDATE case_jobs j SET snapshot_alert_after = NOW() + interval '1 hour' FROM due
      WHERE j.job_id = due.job_id
      RETURNING j.job_id, j.ticket_id, j.snapshot_alert_after::text AS reservation,
        FLOOR(EXTRACT(EPOCH FROM (NOW()-j.snapshot_wait_since))/60)::integer AS minutes
  `);
  const wait = rows[0];
  if (!wait) return;
  const status = `Zendesk 消息已等待 ${wait.minutes} 分钟，工单仍在队列中`;
  console.warn(`[Worker] #${wait.ticket_id} ${status}`);
  if (!webhook) return;
  const sent = await notify(webhook, { ticketId: Number(wait.ticket_id), title: '工单消息同步延迟', status,
    note: '请核对工单聊天历史和 Webhook 消息 ID，确认最新客户消息是否已由坐席答复。', color: 'orange',
  }, (url, init) => fetch(url, { ...init, signal: AbortSignal.timeout(10_000) }));
  if (!sent) await db.query(`UPDATE case_jobs SET snapshot_alert_after = NOW() + interval '2 minutes'
    WHERE job_id = $1 AND snapshot_alert_after = $2::timestamptz`, [wait.job_id, wait.reservation]);
}

export function startQueueAlerts(db: Query, webhook: string) {
  let pending: Promise<void> | undefined;
  const tick = () => {
    if (!pending) pending = notifySnapshotWaits(db, sendFeishuAlert, webhook)
      .catch(error => console.warn('[Worker] Queue alert check failed:', error.message))
      .finally(() => { pending = undefined; });
  };
  const timer = setInterval(tick, 60_000);
  tick();
  return async () => { clearInterval(timer); await pending; };
}
