import type pg from 'pg';
import type { FeishuAlertParams, HumanAlertTracker } from './alerts.js';
import { zendeskTicketUrl } from './internal-draft-delivery.js';
import { ticketIdFromDatabase } from './job-queue.js';
import { detectCustomerLanguage, type ReplyLanguage } from './reply-policy.js';

/**
 * Follow-up work for a customer's request for a human.
 *
 * Requests reach the human_handoffs table two ways: ingress records one in the
 * same transaction that cancels the AI run when a customer replies exactly
 * "human", 人工 or 真人; the worker records one (recordWorkerHandoff) when it
 * detects a request phrased as a sentence. This module claims those rows and,
 * for each one:
 *   1. tags the Zendesk ticket (retried until it succeeds or attempts run out),
 *   2. alerts Feishu once,
 *   3. for the first command of a takeover, sends the customer one
 *      acknowledgement through Browser Relay. That reply is fenced and never
 *      retried, so a customer can never receive it twice.
 */

export const DEFAULT_HANDOFF_TAG = 'human_requested';
export const MAX_HANDOFF_ATTEMPTS = 5;
const HANDOFF_LEASE_SECONDS = 120;

// Support hours are New York Sunday–Thursday nights, 9 PM–6 AM EDT or
// 8 PM–5 AM EST. Both are exactly 01:00–10:00 UTC, Monday–Friday, so the
// window needs no time-zone database and is the same on DST transition days.
const SUPPORT_START_UTC_HOUR = 1;
const SUPPORT_END_UTC_HOUR = 10;

export function isWithinSupportHours(now: Date): boolean {
  const day = now.getUTCDay(); // 0 = Sunday
  const hour = now.getUTCHours();
  return day >= 1 && day <= 5 && hour >= SUPPORT_START_UTC_HOUR && hour < SUPPORT_END_UTC_HOUR;
}

// Keep in sync with customerFooter() in reply-policy.ts (a test checks this).
export const SUPPORT_HOURS_TEXT: Record<ReplyLanguage, string> = {
  en: 'Sunday–Thursday nights, 9:00 PM–6:00 AM EDT (daylight saving time) or 8:00 PM–5:00 AM EST (standard time)',
  zh: '纽约时间：周日至周四晚间，夏令时 21:00 至次日 6:00，标准时间 20:00 至次日 5:00',
  ja: 'ニューヨーク時間：日曜～木曜、夏時間 21:00～翌6:00、標準時間 20:00～翌5:00',
};

export function handoffAcknowledgement(language: ReplyLanguage, inSupportHours: boolean): string {
  if (inSupportHours) {
    switch (language) {
      case 'zh':
        return '您好，已为您转接人工支持，工程师马上就来，请您稍等一会。';
      case 'ja':
        return 'お問い合わせありがとうございます。サポート担当者におつなぎしました。まもなく担当者が対応いたしますので、少々お待ちください。';
      default:
        return 'Thanks for reaching out. Your request has been passed to our human support team, and a support engineer will be with you shortly. Please wait a moment.';
    }
  }
  switch (language) {
    case 'zh':
      return `您好，已为您转接人工支持。当前不在人工服务时间内，人工支持会在服务时间内第一时间查看您的问题（${SUPPORT_HOURS_TEXT.zh}）。`;
    case 'ja':
      return `お問い合わせありがとうございます。サポート担当者におつなぎしました。現在はサポート時間外のため、サポート時間内に担当者が最優先で確認いたします（${SUPPORT_HOURS_TEXT.ja}）。`;
    default:
      return `Thanks for reaching out. Your request has been passed to our human support team. We are currently outside support hours, so a support engineer will review your issue first thing during support hours (New York time): ${SUPPORT_HOURS_TEXT.en}.`;
  }
}

// The footer tells every customer to reply with the English word "human", so
// that word says nothing about their language; 人工 and 真人 still read as Chinese.
const ENGLISH_HUMAN_ONLY = /^[\s\p{P}]*human[\s\p{P}]*$/iu;

export function handoffLanguage(customerMessage: string, conversationContext = ''): ReplyLanguage {
  const message = ENGLISH_HUMAN_ONLY.test(customerMessage) ? '' : customerMessage;
  const context = conversationContext
    .split(/\n\n(?=(?:Customer|Support) \[)/)
    .filter(block => !(block.startsWith('Customer [') &&
      ENGLISH_HUMAN_ONLY.test(block.slice(block.indexOf('\n') + 1))))
    .join('\n\n');
  return detectCustomerLanguage(message, context);
}

/** Add tags to a ticket. Zendesk's PUT .../tags.json adds without removing existing tags. */
export async function addZendeskTicketTags(options: {
  baseUrl: string;
  email: string;
  apiToken: string;
  ticketId: number;
  tags: string[];
  fetchImpl?: typeof fetch;
}): Promise<void> {
  if (!options.email.trim() || !options.apiToken.trim()) {
    throw new Error('Zendesk credentials are missing');
  }
  const url = zendeskTicketUrl(options.baseUrl, options.ticketId, 'tags');
  const authorization = Buffer.from(`${options.email.trim()}/token:${options.apiToken.trim()}`).toString('base64');
  const response = await (options.fetchImpl ?? fetch)(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Basic ${authorization}` },
    body: JSON.stringify({ tags: options.tags }),
    signal: AbortSignal.timeout(15000),
    redirect: 'error',
  });
  if (!response.ok) {
    // Never log the response body: it may contain customer data.
    throw new Error(`Zendesk tag request failed with HTTP ${response.status}`);
  }
}

export interface HandoffRow {
  handoff_id: string;
  ticket_id: number;
  acknowledge: boolean;
  customer_message: string | null;
  attempts: number;
  alerted_at: Date | null;
  tagged_at: Date | null;
  ack_state: string | null;
}

type Queryable = Pick<pg.Pool, 'query'>;

export type WorkerHandoffOutcome = 'recorded' | 'already_locked';

/**
 * Lock the ticket for humans and record a handoff for a request the worker
 * detected. If the ticket is already locked, whoever locked it (usually
 * ingress, for an exact "human") already recorded the handoff, so nothing is
 * added. Only a request in the customer's unanswered messages is acknowledged;
 * one found only in older history is alerted and tagged but not answered.
 */
export async function recordWorkerHandoff(
  pool: Pick<pg.Pool, 'connect'>,
  options: { jobId: string; ticketId: number; customerMessage: string; acknowledge: boolean; reason: string },
): Promise<WorkerHandoffOutcome> {
  const handoffId = `handoff:${options.jobId}`;
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query(
      `INSERT INTO ticket_states (ticket_id, updated_at) VALUES ($1, NOW()) ON CONFLICT (ticket_id) DO NOTHING`,
      [options.ticketId],
    );
    const state = await client.query(
      'SELECT human_cancelled_at FROM ticket_states WHERE ticket_id = $1 FOR UPDATE',
      [options.ticketId],
    );
    if (state.rows[0]?.human_cancelled_at) {
      await client.query('COMMIT');
      return 'already_locked';
    }
    await client.query(
      'UPDATE ticket_states SET human_cancelled_at = NOW(), updated_at = NOW() WHERE ticket_id = $1',
      [options.ticketId],
    );
    // An internal event, so the in-flight monitor does not read it as a new
    // customer message; human_handoffs.handoff_id references it.
    await client.query(`
      INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, payload, received_at)
      VALUES ($1, $2, NOW(), 'pi-agent', 'internal', 'human.handoff_detected', $3::json, NOW())
      ON CONFLICT (event_id) DO NOTHING
    `, [handoffId, options.ticketId, JSON.stringify({ job_id: options.jobId, reason: options.reason })]);
    await client.query(`
      INSERT INTO human_handoffs (handoff_id, ticket_id, acknowledge, customer_message, status, attempts, available_at, created_at, updated_at)
      VALUES ($1, $2, $3, $4, 'pending', 0, NOW(), NOW(), NOW())
      ON CONFLICT (handoff_id) DO NOTHING
    `, [handoffId, options.ticketId, options.acknowledge, options.customerMessage.trim().slice(0, 500) || null]);
    await client.query('COMMIT');
    return 'recorded';
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}

/** Claim the next due handoff, or reclaim one whose lease expired. */
export async function claimNextHandoff(pool: Queryable): Promise<HandoffRow | null> {
  // A row that keeps losing its lease is given up instead of retried forever.
  await pool.query(`
    UPDATE human_handoffs
    SET status = 'failed', lease_expires_at = NULL, updated_at = NOW(),
        last_error = COALESCE(last_error, 'lease_expired_max_attempts_exceeded')
    WHERE status = 'processing' AND lease_expires_at <= NOW() AND attempts >= $1
  `, [MAX_HANDOFF_ATTEMPTS]);
  const res = await pool.query(`
    WITH next AS (
      SELECT handoff_id FROM human_handoffs
      WHERE attempts < $1 AND (
        (status = 'pending' AND available_at <= NOW())
        OR (status = 'processing' AND lease_expires_at <= NOW())
      )
      ORDER BY available_at, created_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE human_handoffs h
    SET status = 'processing', attempts = h.attempts + 1,
        lease_expires_at = NOW() + make_interval(secs => $2), updated_at = NOW()
    FROM next
    WHERE h.handoff_id = next.handoff_id
    RETURNING h.handoff_id, h.ticket_id, h.acknowledge, h.customer_message,
              h.attempts, h.alerted_at, h.tagged_at, h.ack_state
  `, [MAX_HANDOFF_ATTEMPTS, HANDOFF_LEASE_SECONDS]);
  const row = res.rows[0];
  if (!row) return null;
  return { ...row, ticket_id: ticketIdFromDatabase(row.ticket_id) } as HandoffRow;
}

export interface HandoffDependencies {
  pool: Queryable;
  tag: string;
  now: () => Date;
  alertTracker: Pick<HumanAlertTracker, 'shouldAlert' | 'recordAlert'>;
  sendAlert: (params: FeishuAlertParams) => Promise<void>;
  addTags: (ticketId: number, tags: string[]) => Promise<void>;
  /** The public conversation, used only to pick the reply language. */
  loadConversation: (ticketId: number) => Promise<{ message: string; conversationContext: string }>;
  /** Send one public reply; throws unless the relay reports it as sent. */
  sendReply: (ticketId: number, body: string) => Promise<void>;
  log?: Pick<Console, 'log' | 'warn'>;
}

function errorText(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).slice(0, 500);
}

export async function processHandoff(row: HandoffRow, deps: HandoffDependencies): Promise<void> {
  const log = deps.log ?? console;
  const ticketId = row.ticket_id;
  const inHours = isWithinSupportHours(deps.now());
  const customerMessage = row.customer_message?.trim() || 'human';

  // 1. Tag. The only step that is retried.
  let tagError: string | undefined;
  if (!row.tagged_at) {
    try {
      await deps.addTags(ticketId, [deps.tag]);
      await deps.pool.query(
        'UPDATE human_handoffs SET tagged_at = NOW(), updated_at = NOW() WHERE handoff_id = $1',
        [row.handoff_id],
      );
    } catch (error) {
      tagError = errorText(error);
      log.warn(`[Handoff] Tagging ticket #${ticketId} failed (attempt ${row.attempts}/${MAX_HANDOFF_ATTEMPTS}): ${tagError}`);
    }
  }

  // 2. Alert once. A repeated command inside the cooldown is not re-announced,
  // but the first command of a takeover is always announced.
  if (!row.alerted_at) {
    if (row.acknowledge || deps.alertTracker.shouldAlert(ticketId)) {
      deps.alertTracker.recordAlert(ticketId);
      const tagLine = tagError
        ? `打标签 ${deps.tag} 失败，将自动重试：${tagError}`
        : `已给工单打标签 ${deps.tag}。`;
      const ackLine = row.acknowledge
        ? `AI 将自动回复客户：${inHours ? '人工支持马上就来，请稍等' : '当前不在支持时间，人工会在支持时间内第一时间查看'}。`
        : '本次不自动回复客户：客户此前已请求过人工，或转人工诉求只出现在较早的对话中。';
      await deps.sendAlert({
        title: '🙋 Dify 工单转人工接管提醒',
        ticketId,
        status: inHours
          ? '客户请求人工支持，AI 已停止回复（当前在支持时间内，请尽快跟进）'
          : '客户请求人工支持，AI 已停止回复（当前不在支持时间内）',
        customerMessage: customerMessage.slice(0, 500),
        note: `${tagLine}\n${ackLine}`,
      });
    } else {
      log.log(`[Handoff] Alert for ticket #${ticketId} suppressed (cooldown active).`);
    }
    await deps.pool.query(
      'UPDATE human_handoffs SET alerted_at = NOW(), updated_at = NOW() WHERE handoff_id = $1',
      [row.handoff_id],
    );
  }

  // 3. Acknowledge the customer at most once.
  if (row.acknowledge && row.ack_state === 'attempting') {
    // A previous attempt stopped mid-send: the customer may already have it.
    await deps.pool.query(
      `UPDATE human_handoffs SET ack_state = 'uncertain', updated_at = NOW() WHERE handoff_id = $1`,
      [row.handoff_id],
    );
    await deps.sendAlert({
      title: '⚠️ 转人工确认消息状态未知',
      ticketId,
      status: '确认消息可能未发送',
      note: '发送转人工确认消息时 worker 中断，无法确认客户是否收到。为避免重复发送，系统不会重试，请人工检查并回复客户。',
    });
  } else if (row.acknowledge && !row.ack_state) {
    let language: ReplyLanguage;
    try {
      const conversation = await deps.loadConversation(ticketId);
      language = handoffLanguage(customerMessage, conversation.conversationContext);
    } catch (error) {
      log.warn(`[Handoff] Could not load ticket #${ticketId} to pick the reply language: ${errorText(error)}`);
      language = handoffLanguage(customerMessage);
    }
    const body = handoffAcknowledgement(language, inHours);
    const claimed = await deps.pool.query(
      `UPDATE human_handoffs SET ack_state = 'attempting', updated_at = NOW()
       WHERE handoff_id = $1 AND ack_state IS NULL RETURNING handoff_id`,
      [row.handoff_id],
    );
    if ((claimed.rowCount ?? 0) === 1) {
      try {
        await deps.sendReply(ticketId, body);
        await deps.pool.query(
          `UPDATE human_handoffs SET ack_state = 'sent', ack_sent_at = NOW(), updated_at = NOW() WHERE handoff_id = $1`,
          [row.handoff_id],
        );
        log.log(`[Handoff] Acknowledged human request on ticket #${ticketId} (${language}, ${inHours ? 'in' : 'outside'} support hours).`);
      } catch (error) {
        const reason = errorText(error);
        await deps.pool.query(
          `UPDATE human_handoffs SET ack_state = 'failed', updated_at = NOW() WHERE handoff_id = $1`,
          [row.handoff_id],
        );
        await deps.sendAlert({
          title: '🚨 转人工确认消息发送失败',
          ticketId,
          status: '未能自动回复客户',
          reason,
          draftReply: body,
          note: '客户已请求人工，但自动确认消息发送失败。为避免重复发送，系统不会重试，请人工回复客户。',
        });
      }
    }
  }

  // 4. Finish, or schedule another tagging attempt.
  if (!tagError) {
    await deps.pool.query(
      `UPDATE human_handoffs SET status = 'completed', lease_expires_at = NULL, last_error = NULL, updated_at = NOW()
       WHERE handoff_id = $1`,
      [row.handoff_id],
    );
    return;
  }
  if (row.attempts >= MAX_HANDOFF_ATTEMPTS) {
    await deps.pool.query(
      `UPDATE human_handoffs SET status = 'failed', lease_expires_at = NULL, last_error = $2, updated_at = NOW()
       WHERE handoff_id = $1`,
      [row.handoff_id, tagError],
    );
    await deps.sendAlert({
      title: 'ℹ️ 转人工工单打标签失败（低优先级）',
      ticketId,
      status: `打标签 ${deps.tag} 已重试 ${row.attempts} 次仍失败`,
      reason: tagError,
      note: '转人工提醒和客户确认不受影响，请手动给工单加上该标签。',
    });
    return;
  }
  await deps.pool.query(
    `UPDATE human_handoffs SET status = 'pending', lease_expires_at = NULL, last_error = $2,
       available_at = NOW() + make_interval(secs => $3), updated_at = NOW()
     WHERE handoff_id = $1`,
    [row.handoff_id, tagError, 30 * row.attempts],
  );
}

/** Poll and process handoffs one at a time until isRunning() turns false. */
export async function runHandoffLoop(options: {
  pool: Queryable;
  isRunning: () => boolean;
  process: (row: HandoffRow) => Promise<void>;
  idleMs?: number;
  sleep?: (ms: number) => Promise<void>;
}): Promise<void> {
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms)));
  while (options.isRunning()) {
    try {
      const row = await claimNextHandoff(options.pool);
      if (!row) {
        await sleep(options.idleMs ?? 1500);
        continue;
      }
      try {
        await options.process(row);
      } catch (error) {
        // The row keeps its lease and is reclaimed once it expires.
        console.error(`[Handoff] Processing ${row.handoff_id} for ticket #${row.ticket_id} failed:`, errorText(error));
      }
    } catch (error) {
      console.error('[Handoff] Polling failed:', errorText(error));
      await sleep(2000);
    }
  }
}
