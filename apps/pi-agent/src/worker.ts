import { randomUUID } from 'node:crypto';
import { activeModelClaimDelay, isModelQuotaError, modelEndpointKey, postponeJobForModelQuota, prepareModelEndpoint, recordModelRecovery } from './endpoint-cooldown.js';
import { oldestWaitingTicket, startQueueAlerts } from './queue-alerts.js';
import http from 'node:http';
import fs from 'node:fs';
import pg from 'pg';
import dotenv from 'dotenv';
import { CustomTool } from './mcp.js';
import {
  createOpenConnectorServiceConfig,
  describeReadToolDirectory,
  discoverOpenConnectorTools,
  startReadToolDirectoryRefresh,
} from './openconnector.js';
import { runPiSupportAgent } from './agent.js';
import { ModelConfig, parseModelConfig } from './model-config.js';
import { sumRoundTokens } from './round-telemetry.js';
import { findPublicDeliveryEvidence, sendBrowserRelay } from './relay.js';
import { loadConversation } from './conversation.js';
import { deliverInternalDraft, formatInternalDraft } from './internal-draft-delivery.js';
import { detectCustomerLanguage } from './reply-policy.js';
import { sendFeishuAlert, HumanAlertTracker } from './alerts.js';
import { getTicketDetails as readTicketDetails } from './ticket-context.js';
import { ticketContextSettings } from './ticket-context-settings.js';
import {
  claimNextJob, claimPublicDeliveryCheck, completePreviouslySentPublicJob, confirmPublicDelivery,
  deferUnconfirmedConversation, deliverWithCustomerCoverage, finishPublicDeliveryCheck, loadCustomerCoverage,
  markPublicDeliveryUncertain, recordStaffCoverage, requeueLatestConversation,
} from './job-queue.js';
import {
  addZendeskTicketTags, DEFAULT_HANDOFF_TAG, HANDOFF_ALERT_TIMEOUT_MS, HANDOFF_CONVERSATION_TIMEOUT_MS,
  HANDOFF_REPLY_TIMEOUT_MS, processHandoff, recordWorkerHandoff, runHandoffLoop,
} from './human-handoff.js';

dotenv.config();

function loadCredentialFile(filePath: string) {
  if (!fs.existsSync(filePath)) return false;
  try {
    const content = fs.readFileSync(filePath, 'utf8');
    let loadedCount = 0;
    for (const line of content.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eqIdx = trimmed.indexOf('=');
      if (eqIdx <= 0) continue;
      const key = trimmed.slice(0, eqIdx).trim();
      let val = trimmed.slice(eqIdx + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      if (key) {
        process.env[key] = val;
        loadedCount++;
      }
    }
    console.log(`[Worker] Loaded ${loadedCount} credentials from ${filePath}`);
    return true;
  } catch (err: any) {
    console.warn(`[Worker] Failed to read ${filePath}:`, err.message);
    return false;
  }
}

for (const credPath of ['/app/credentials.env', './credentials.env', '../credentials.env', '../../credentials.env']) {
  if (loadCredentialFile(credPath)) {
    break;
  }
}

function cleanEnv(val?: string): string {
  if (!val) return '';
  let s = val.trim();
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    s = s.slice(1, -1);
  }
  return s.trim();
}

const WORKER_ID = `pi-agent-${process.pid}-${Math.random().toString(36).slice(2, 8)}`;
const PORT = parseInt(cleanEnv(process.env.SERVICE_PORT) || '8102', 10);
const rawDbUrl = cleanEnv(process.env.DATABASE_URL);
const DATABASE_URL = rawDbUrl.replace(/^postgresql\+[a-zA-Z0-9_]+:\/\//, 'postgresql://');
const BROWSER_RELAY_URL = cleanEnv(process.env.CASEWORK_BROWSER_RELAY_URL) || 'https://zendesk.smlershou.top/zendesk-browser-relay';
const BROWSER_RELAY_KEY = cleanEnv(process.env.CASEWORK_BROWSER_RELAY_API_KEY);
const ZENDESK_BASE_URL = cleanEnv(process.env.ZENDESK_BASE_URL);
const ZENDESK_EMAIL = cleanEnv(process.env.ZENDESK_EMAIL);
const ZENDESK_API_TOKEN = cleanEnv(process.env.ZENDESK_API_TOKEN);
const OPENCONNECTOR_MCP_URL = cleanEnv(process.env.CASEWORK_OPENCONNECTOR_MCP_URL) || 'https://open-connector.yyg066340.workers.dev/mcp';
const OPENCONNECTOR_RUNTIME_TOKEN = cleanEnv(process.env.CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME)
  || cleanEnv(process.env.OPENCONNECTOR_RUNTIME_TOKEN)
  || cleanEnv(process.env.OOMOL_CONNECT_RUNTIME_TOKEN);
const FEISHU_WEBHOOK_URL = cleanEnv(process.env.FEISHU_WEBHOOK_URL);
const humanAlertTracker = new HumanAlertTracker(30 * 60 * 1000);
const HUMAN_HANDOFF_TAG = cleanEnv(process.env.CASEWORK_HUMAN_HANDOFF_TAG) || DEFAULT_HANDOFF_TAG;

async function notifyHumanTakeover(ticketId: number, customerMessage: string, reason: string): Promise<void> {
  if (!humanAlertTracker.shouldAlert(ticketId)) {
    console.log(`[Worker] Human takeover alert for ticket #${ticketId} suppressed (cooldown active).`);
    return;
  }
  humanAlertTracker.recordAlert(ticketId);
  await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
    title: `🙋 工单 #${ticketId} 需要人工处理`,
    color: 'red',
    ticketId,
    status: '客户请求人工支持，AI 已停止回复，请尽快跟进。',
    customerMessage: customerMessage ? customerMessage.slice(0, 500) : '客户请求人工支持',
    note: reason || '检测到客户诉求转人工或已有真人介入，AI 已自动暂停并移交，请真人客服及时跟进。',
  });
}

if (!DATABASE_URL) {
  console.error('[Worker] Fatal: DATABASE_URL is required');
  process.exit(1);
}

const pool = new pg.Pool({ connectionString: DATABASE_URL });


async function getTicketDetails(ticketId: number, service: Parameters<typeof readTicketDetails>[1],
  signal?: AbortSignal, coverage?: Parameters<typeof readTicketDetails>[3]) {
  const settings = await ticketContextSettings(pool, ticketId);
  const details = await readTicketDetails(ticketId, service, signal, coverage, settings);
  return { ...details, observedResume: settings.handoffResume ?? null };
}

async function getActiveModelConfig(): Promise<ModelConfig> {
  const res = await pool.query("SELECT value FROM system_settings WHERE key = 'model_config'");
  return parseModelConfig(res.rows[0]?.value);
}

async function recordJobOutcome(opts: {
  jobId: string;
  ticketId: number;
  generation?: number;
  status: string;
  startedAt: Date;
  finishedAt: Date;
  trace: any | null;
  delivery: any | null;
  errorReason?: string;
  // Operator-mention drafts go to an internal note, never final_reply.
  internalDraft?: string;
}) {
  const sessionId = `pi-session-${opts.jobId}`;
  const toolCallsCount = opts.trace?.toolCalls?.length ?? 0;
  const toolCallRecords = opts.trace?.steps ?? [];
  const activeQuestion = {
    ticket_id: opts.ticketId,
    customer_message: opts.trace?.customerMessage ?? '',
    requester_email: opts.trace?.requesterEmail ?? '',
    model: opts.trace?.model ?? '',
  };
  const outcome = {
    status: opts.status,
    final_reply: opts.trace?.finalReply ?? '',
    internal_draft: opts.internalDraft ?? '',
    delivery: opts.delivery ?? null,
    error: opts.errorReason ?? null,
    total_duration_ms: opts.trace?.totalDurationMs ?? (opts.finishedAt.getTime() - opts.startedAt.getTime()),
    rounds: opts.trace?.rounds ?? null,
    tokens: opts.trace?.tokens ?? null,
  };

  try {
    await pool.query(
      `
      INSERT INTO run_records (
        session_id, job_id, ticket_id, generation, status,
        tool_calls, tool_call_records, active_question, outcome,
        created_at, started_at, finished_at, updated_at
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
      ON CONFLICT (job_id) DO UPDATE SET
        status = EXCLUDED.status,
        tool_calls = EXCLUDED.tool_calls,
        tool_call_records = EXCLUDED.tool_call_records,
        active_question = EXCLUDED.active_question,
        outcome = EXCLUDED.outcome,
        finished_at = EXCLUDED.finished_at,
        updated_at = NOW()
      WHERE run_records.outcome->'delivery'->>'status' IS DISTINCT FROM 'sent'
        OR EXCLUDED.outcome->'delivery'->>'status' = 'sent'
    `,
      [
        sessionId,
        opts.jobId,
        opts.ticketId,
        opts.generation || 1,
        opts.status,
        toolCallsCount,
        JSON.stringify(toolCallRecords),
        JSON.stringify(activeQuestion),
        JSON.stringify(outcome),
        opts.startedAt,
        opts.startedAt,
        opts.finishedAt,
      ]
    );
    console.log(`[Worker] Persisted trace and outcome to run_records for job ${opts.jobId} (status=${opts.status})`);
  } catch (err: any) {
    console.error(`[Worker] Error recording outcome in run_records:`, err.message);
  }
}

type ClaimedJob = { job_id: string; lease_owner: string; attempts: number };

async function markJobSucceeded(job: ClaimedJob) {
  await finishJob(job, 'completed');
}

async function markJobFailed(job: ClaimedJob, reason: string) {
  await finishJob(job, 'failed', reason);
}

async function markJobCancelled(job: ClaimedJob, reason: string) {
  await finishJob(job, 'cancelled', reason);
}

async function finishJob(job: ClaimedJob, status: string, reason?: string) {
  await pool.query(`
    UPDATE case_jobs
    SET status = $4, dead_letter_reason = $5, finished_at = NOW(), updated_at = NOW(),
        lease_owner = NULL, lease_expires_at = NULL
    WHERE job_id = $1 AND lease_owner = $2 AND attempts = $3 AND status = 'running'
  `, [job.job_id, job.lease_owner, job.attempts, status, reason?.slice(0, 1024) ?? null]);
}

async function isTicketHumanCancelled(ticketId: number): Promise<boolean> {
  try {
    const res = await pool.query(
      'SELECT human_cancelled_at FROM ticket_states WHERE ticket_id = $1',
      [ticketId]
    );
    return Boolean(res.rows[0]?.human_cancelled_at);
  } catch (err: any) {
    console.warn(`[Worker] Failed to query ticket_states for ticket #${ticketId}:`, err.message);
    return false;
  }
}

/**
 * Lock the ticket and record a handoff for a human request the worker detected,
 * so the handoff loop tags it, alerts Feishu and (for a new request) answers the
 * customer. Falls back to a plain Feishu alert if the handoff cannot be recorded.
 */
async function handOffToHuman(
  job: ClaimedJob & { ticket_id: number; generation: number },
  details: Awaited<ReturnType<typeof getTicketDetails>>,
  reason: string,
): Promise<'superseded' | void> {
  try {
    const outcome = await recordWorkerHandoff(pool, {
      jobId: job.job_id,
      ticketId: job.ticket_id,
      customerMessage: details.humanRequestMessage ?? details.message,
      leaseOwner: job.lease_owner, attempt: job.attempts, generation: job.generation,
      acknowledge: Boolean(details.latestRequestedHuman),
      observedResume: details.observedResume, requestOccurredAt: details.humanRequestOccurredAt,
      reason,
    });
    if (outcome === 'lease_lost') return 'superseded';
    if (outcome === 'resume_changed') {
      // A staff resume won after the history read. A fresh claim re-evaluates the current messages.
      if (!await deferUnconfirmedConversation(pool, job, job.lease_owner, job.attempts, 'handoff_resume_changed')) {
        await markJobCancelled(job, 'handoff_resume_changed');
      }
      return 'superseded';
    }
    console.warn(`[Worker] 🛡️ HUMAN TAKEOVER for ticket #${job.ticket_id} (${outcome}): ${reason}`);
  } catch (error: any) {
    console.warn(`[Worker] Failed to record human handoff for ticket #${job.ticket_id}:`, error.message);
    await notifyHumanTakeover(job.ticket_id, details.message, reason);
  }
}

async function main() {
  console.log(`[Worker] Starting Pi Agent Worker (${WORKER_ID}) on port ${PORT}...`);

  // Start HTTP healthcheck server
  const server = http.createServer((req, res) => {
    if (req.url === '/healthz' || req.url === '/readyz') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ready', worker: WORKER_ID, service: 'pi-agent' }));
      return;
    }
    res.writeHead(404);
    res.end();
  });
  server.listen(PORT, '0.0.0.0', () => {
    console.log(`[Worker] Healthcheck listening on http://0.0.0.0:${PORT}/readyz`);
  });

  if (!OPENCONNECTOR_RUNTIME_TOKEN) {
    throw new Error('CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME is required');
  }
  if (!FEISHU_WEBHOOK_URL) {
    console.warn('[Worker] FEISHU_WEBHOOK_URL is not configured; human takeover and failure alerts are disabled.');
  }
  const openConnectorService = createOpenConnectorServiceConfig(
    OPENCONNECTOR_MCP_URL,
    OPENCONNECTOR_RUNTIME_TOKEN,
  );
  const allTools: CustomTool[] = await discoverOpenConnectorTools(openConnectorService);
  if (allTools.length === 0) {
    throw new Error('OpenConnector exposed no permitted investigation tools');
  }
  console.log(`[Worker] Loaded ${allTools.length} read-only investigation controls from OpenConnector`);
  // Keep the connected read-only directory warm so each run gets it in its prompt.
  const stopDirectoryRefresh = startReadToolDirectoryRefresh(openConnectorService);

  const MAX_CONCURRENT_JOBS = parseInt(cleanEnv(process.env.CASEWORK_WORKER_CONCURRENCY) || '2', 10);
  console.log(`[Worker] Concurrency limit initialized to ${MAX_CONCURRENT_JOBS} parallel jobs`);

  interface ActiveJobContext {
    jobId: string;
    ticketId: number;
    abortController: AbortController;
    startedAt: number;
    completion?: Promise<void>;
  }
  const activeJobs = new Map<string, ActiveJobContext>();

  // Main polling loop
  let running = true;
  const shutdown = () => {
    console.log('[Worker] Graceful shutdown initiated. Aborting active jobs...');
    running = false;
    for (const ctx of activeJobs.values()) {
      console.log(`[Worker] Aborting active job ${ctx.jobId} for ticket #${ctx.ticketId}...`);
      ctx.abortController.abort();
    }
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  // Customer "human" commands recorded by ingress: tag, alert, acknowledge.
  const stopQueueAlerts = startQueueAlerts(pool, FEISHU_WEBHOOK_URL);
  const handoffLoop = runHandoffLoop({
    pool,
    isRunning: () => running,
    process: row => processHandoff(row, {
      pool,
      tag: HUMAN_HANDOFF_TAG,
      now: () => new Date(),
      alertTracker: humanAlertTracker,
      // With no webhook configured, alerts are disabled, not undelivered.
      sendAlert: async params => !FEISHU_WEBHOOK_URL || sendFeishuAlert(FEISHU_WEBHOOK_URL, params,
        (url, init) => fetch(url, { ...init, signal: AbortSignal.timeout(HANDOFF_ALERT_TIMEOUT_MS) })),
      addTags: (ticketId, tags) => addZendeskTicketTags({
        baseUrl: ZENDESK_BASE_URL, email: ZENDESK_EMAIL, apiToken: ZENDESK_API_TOKEN, ticketId, tags,
      }),
      loadConversation: ticketId => getTicketDetails(
        ticketId, openConnectorService, AbortSignal.timeout(HANDOFF_CONVERSATION_TIMEOUT_MS),
      ),
      sendReply: async (ticketId, body) => {
        // The relay's "reply human" footer would ask a customer who just did so to do it again.
        await sendBrowserRelay(BROWSER_RELAY_URL, BROWSER_RELAY_KEY, ticketId, body, {
          signal: AbortSignal.timeout(HANDOFF_REPLY_TIMEOUT_MS),
          appendFooter: false,
        });
      },
    }),
  });

  async function reconcilePublicDelivery() {
    const delivery = await claimPublicDeliveryCheck(pool);
    if (!delivery) return;
    let evidence;
    let reason = delivery.last_error ?? 'public_delivery_confirmation_missing_after_dispatch';
    try {
      const conversation = await loadConversation(delivery.ticket_id, openConnectorService, AbortSignal.timeout(20_000));
      evidence = findPublicDeliveryEvidence(conversation, delivery.delivery_id, delivery.started_at);
    } catch (error) {
      if (!delivery.last_error) reason = 'public_delivery_history_unavailable';
      console.warn(`[Worker] Public delivery history check failed for ${delivery.delivery_id}`,
        error instanceof Error ? error.name : 'unknown error');
    }
    if (evidence) {
      await confirmPublicDelivery(pool, delivery, evidence);
      console.log(`[Worker] Reconciled public delivery ${delivery.delivery_id} for ticket #${delivery.ticket_id}`);
      return;
    }

    const unresolved = await markPublicDeliveryUncertain(pool, delivery, reason);
    if (unresolved?.status !== 'uncertain') return;
    let alerted = false;
    if (!delivery.alert_sent_at && FEISHU_WEBHOOK_URL) {
      alerted = await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
        title: '🚨 Zendesk 公开回复投递待核实', color: 'orange',
        ticketId: delivery.ticket_id, status: '可能已发送，自动公开回复已暂停',
        note: `投递编号 ${delivery.delivery_id}。请先在 Zendesk 核实对话和发送记录，再决定是否回复。`,
      }, (url, init) => fetch(url, { ...init, signal: AbortSignal.timeout(HANDOFF_ALERT_TIMEOUT_MS) }));
    }
    await finishPublicDeliveryCheck(pool, delivery.delivery_id, alerted);
  }

  async function processJob(
    job: any,
    abortController: AbortController,
  ): Promise<void> {
    const startTime = Date.now();
    console.log(`[Worker] 🚀 Processing job ${job.job_id} for ticket #${job.ticket_id} (active concurrency: ${activeJobs.size}/${MAX_CONCURRENT_JOBS})`);

    const isMentionJob = Boolean(
      job.is_operator_mention ||
      job.event_id?.startsWith('mention:') ||
      job.expected_event_id?.startsWith('mention:')
    );

    let activeTrace: any = null;
    let monitorTimer: ReturnType<typeof setInterval> | undefined;

    // The job is cancelled once a newer customer message is seen; queueing the
    // successor is follow-up work. If it fails, the run is still cancelled (not
    // failed), the reason says why the successor is missing, and a low-priority
    // alert (not the failure alert) is sent; ingress still queues a job when it
    // records that customer message.
    async function requeueAfterInterception(
      latest: { lastEventId?: string; message?: string; readCustomerMessages?: Awaited<ReturnType<typeof getTicketDetails>>['readCustomerMessages'] },
      interceptReason: string,
    ): Promise<string> {
      try {
        const requeued = await requeueLatestConversation(pool, {
          job,
          latestZendeskEventId: latest.lastEventId,
          observedCustomers: latest.readCustomerMessages,
          lease: { workerId: job.lease_owner, attempt: job.attempts },
          reason: interceptReason,
        });
        console.log(`[Worker] Requeued ticket #${job.ticket_id} as job ${requeued.jobId} (${requeued.outcome}) after intercepting job ${job.job_id}.`);
        return interceptReason;
      } catch (requeueErr: any) {
        console.error(`[Worker] Failed to requeue ticket #${job.ticket_id} after intercepting job ${job.job_id}:`, requeueErr.message);
        try {
          await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
            title: 'ℹ️ Dify 工单重新排队失败提醒（低优先级）',
            ticketId: job.ticket_id,
            status: '旧答复已取消（未发送），自动重新排队失败',
            reason: requeueErr.message,
            customerMessage: latest.message ? latest.message.slice(0, 500) : undefined,
            note: '客户在 AI 推理期间发送了新消息，旧答复已作废。ingress 记录该消息后通常会自动排队处理；如稍后仍无 AI 回复，请人工跟进。',
          });
        } catch (alertErr: any) {
          console.warn(`[Worker] Failed to send requeue alert for ticket #${job.ticket_id}:`, alertErr.message);
        }
        return `${interceptReason}（重新排队失败：${requeueErr.message}）`;
      }
    }
    // Context loading is also leased work, and its failures must reach the same
    // terminal-state handler as model and delivery failures.
    const leaseTimer = setInterval(() => {
      pool.query(`UPDATE case_jobs SET lease_expires_at = NOW() + interval '180 seconds',
        heartbeat_at = NOW(), updated_at = NOW() WHERE job_id = $1 AND status = 'running'
        AND lease_owner = $2 AND attempts = $3`, [job.job_id, job.lease_owner, job.attempts])
        .catch((error: Error) => console.warn('[Worker] Lease heartbeat failed:', error.message));
    }, 60_000);
    try {
      if (!isMentionJob && await completePreviouslySentPublicJob(pool, job, job.lease_owner, job.attempts)) return;

      // Guard 1: Has this ticket already been transferred to human in database?
      // NOTE: Mention jobs BYPASS this guard because an operator explicitly tagged @David Yang!
      const isHumanInDb = await isTicketHumanCancelled(job.ticket_id);
      if (isHumanInDb && !isMentionJob) {
        const humanReason = `模式 B 人工优先保护：该工单此前已转人工客服，AI 坚决不介入。`;
        console.warn(`[Worker] 🛡️ HUMAN TAKEOVER: ${humanReason}`);
        if (humanAlertTracker.shouldAlert(job.ticket_id)) {
          const details = await getTicketDetails(job.ticket_id, openConnectorService);
          await notifyHumanTakeover(job.ticket_id, details.message, humanReason);
        }
        await markJobCancelled(job, humanReason);
        return;
      }

      const coverage = isMentionJob ? null : await loadCustomerCoverage(pool, job.ticket_id);
      // An opaque delivery ID is distinct from a typed Zendesk message cursor.
      const expected = job.expected_event_id;
      if (coverage && /^(?:messaging|zendesk):[^:]+$/.test(expected ?? '') && !coverage.coveredCursors.has(expected)) {
        coverage.pendingCursors.add(expected);
      }
      const initialDetails = await getTicketDetails(job.ticket_id, openConnectorService, abortController.signal, coverage ?? undefined);
      if (initialDetails.staffAnsweredCursors?.length) await recordStaffCoverage(pool, job.ticket_id, initialDetails.staffAnsweredCursors);
      const readCursors = new Set(initialDetails.readCustomerMessages?.flatMap(message => message.cursors));
      if (coverage) {
        const afterRead = await loadCustomerCoverage(pool, job.ticket_id);
        const missingExpected = initialDetails.visibleCustomerCursors && [...coverage.pendingCursors].some(cursor =>
          !initialDetails.visibleCustomerCursors.has(cursor) && (cursor === expected || readCursors.size === 0 || initialDetails.contactFormOnly));
        const admissionAfterRead = afterRead.pending.some(({ seq, cursor }) =>
          seq > coverage.latestSeq && (!cursor || !readCursors.has(cursor)));
        const unknownPending = initialDetails.isAlreadyAnswered && !coverage.coveredCursors.has(expected) &&
          coverage.pending.some(({ cursor }) => !cursor);
        if (missingExpected || admissionAfterRead || unknownPending) {
          if (!await deferUnconfirmedConversation(pool, job, job.lease_owner, job.attempts)) {
            await markJobCancelled(job, 'customer_conversation_snapshot_unconfirmed');
          }
          return;
        }
      }

      if (initialDetails.hasRequestedHuman && !isMentionJob) {
        const humanReason = '客户请求人工支持，已停止自动回复。';
        if (await handOffToHuman(job, initialDetails, humanReason) === 'superseded') return;
        await markJobCancelled(job, humanReason);
        return;
      }
      if (initialDetails.isAlreadyAnswered && !isMentionJob) {
        await markJobCancelled(job, 'customer_message_already_answered');
        return;
      }

      if (!isMentionJob && initialDetails.unrepliedCount === 0) {
        await recordJobOutcome({
          jobId: job.job_id, ticketId: job.ticket_id, generation: job.generation,
          status: 'awaiting_customer', startedAt: new Date(startTime), finishedAt: new Date(),
          trace: null, delivery: null,
        });
        await finishJob(job, 'awaiting_customer');
        return;
      }

      // If this is a mention job, extract the operator's note instructions
      let effectiveCustomerMessage = initialDetails.message;
      const publicReplyAllowed = !isMentionJob;
      if (isMentionJob) {
        try {
          const evRes = await pool.query(
            'SELECT payload FROM events WHERE event_id = $1 LIMIT 1',
            [job.event_id]
          );
          const operatorInstruction = evRes.rows[0]?.payload?.body;
          if (typeof operatorInstruction !== 'string' || !operatorInstruction.trim()) {
            throw new Error('Missing operator instruction');
          }
          if (operatorInstruction) {
            effectiveCustomerMessage = `[来自人工座席内部备注 @David Yang 的直接指令]:\n"${operatorInstruction}"\n\n[工单客户最新消息]:\n"${initialDetails.message}"`;
            console.log(`[Worker] Injected operator instruction for ticket #${job.ticket_id}`);
          }
        } catch (e: any) {
          // Fail closed: do not answer a customer without the operator instruction.
          throw new Error('Could not load operator mention instruction');
        }
      }

      // Active in-flight monitor & heartbeat
      monitorTimer = setInterval(async () => {
        try {
          if (!isMentionJob && !abortController.signal.aborted) {
            if (await isTicketHumanCancelled(job.ticket_id)) {
              console.warn(`[Worker] In-flight abort: human takeover for ticket #${job.ticket_id}`);
              abortController.abort();
              return;
            }
            const latestCoverage = await loadCustomerCoverage(pool, job.ticket_id);
            if (latestCoverage.pending.some(({ seq, cursor }) =>
              seq > coverage!.latestSeq && (!cursor || !readCursors.has(cursor)))) {
              console.warn(`[Worker] In-flight abort: new customer message for ticket #${job.ticket_id}`);
              abortController.abort();
            }
          }
        } catch (e: any) {
          console.warn(`[Worker] Error in in-flight monitor loop for job ${job.job_id}:`, e.message);
        }
      }, 5000);

      const modelConfig = await getActiveModelConfig();
      const endpointKey = modelEndpointKey(modelConfig.baseUrl);
      const lease = { jobId: job.job_id, ticketId: job.ticket_id, leaseOwner: job.lease_owner };
      const preparation = await prepareModelEndpoint(pool, endpointKey, lease);
      if (preparation.outcome !== 'ready') return;
      console.log(`[Worker] Running Pi Agent for ticket #${job.ticket_id} (Model: ${modelConfig.model}${isMentionJob ? ', Trigger: operator mention' : ''})`);
      const toolDirectory = await describeReadToolDirectory(openConnectorService);

      let agentResult: any;
      try {
        agentResult = await runPiSupportAgent({
          ticketId: job.ticket_id,
          customerMessage: effectiveCustomerMessage,
          conversationContext: initialDetails.conversationContext,
          contextPrepared: true,
          publicReplyAllowed,
          replyLanguage: publicReplyAllowed
            ? detectCustomerLanguage(initialDetails.message, initialDetails.conversationContext)
            : 'en',
          requesterEmail: initialDetails.email,
          tools: allTools,
          toolDirectory,
          apiKey: modelConfig.apiKey,
          modelBaseUrl: modelConfig.baseUrl,
          modelName: modelConfig.model,
          reasoning: modelConfig.reasoning,
          abortSignal: abortController.signal,
          attachments: initialDetails.attachments,
          onRoundTelemetry: (rounds) => {
            // Keep metrics if the run aborts or fails before a final trace exists.
            activeTrace = {
              model: modelConfig.model,
              rounds: [...rounds],
              tokens: sumRoundTokens([...rounds]),
            };
          },
          onTrace: (trace) => {
            activeTrace = trace;
          },
        });
        activeTrace = agentResult.trace;
        if (preparation.observedWindowId && agentResult.trace?.rounds?.length) {
          await recordModelRecovery(pool, endpointKey, preparation.observedWindowId)
            .catch(error => console.warn('[Worker] Could not reset model cooldown count:', error.message));
        }
        console.log(`[Worker] Pi Agent completed reasoning for ticket #${job.ticket_id} in ${Date.now() - startTime}ms (${agentResult.trace.toolCalls.length} tool calls).`);
      } catch (agentErr: any) {
        if (abortController.signal.aborted || agentErr.name === 'AbortError') {
          console.warn(`[Worker] 🚨 Job ${job.job_id} for ticket #${job.ticket_id} was aborted in-flight.`);
          const freshCoverage = isMentionJob ? null : await loadCustomerCoverage(pool, job.ticket_id);
          const freshDetails = await getTicketDetails(job.ticket_id, openConnectorService, undefined, freshCoverage ?? undefined);
          if (freshDetails.staffAnsweredCursors?.length) await recordStaffCoverage(pool, job.ticket_id, freshDetails.staffAnsweredCursors);
          if (freshDetails.hasRequestedHuman && !isMentionJob) {
            const humanReason = `模式 B 人工优先保护：推理中途检测到客户转人工诉求，旧答复已即时打断并移交人工。`;
            if (await handOffToHuman(job, freshDetails, humanReason) === 'superseded') return;
            await recordJobOutcome({
              jobId: job.job_id,
              ticketId: job.ticket_id,
              generation: job.generation,
              status: 'cancelled',
              startedAt: new Date(startTime),
              finishedAt: new Date(),
              trace: activeTrace,
              delivery: null,
              errorReason: humanReason,
            });
            await markJobCancelled(job, humanReason);
            return;
          } else {
            const changed = freshDetails.lastEventId !== initialDetails.lastEventId ||
              freshDetails.message !== initialDetails.message || freshCoverage?.pending.some(({ seq, cursor }) =>
                seq > coverage!.latestSeq && (!cursor || !readCursors.has(cursor)));
            const reason = changed && !isMentionJob
              ? await requeueAfterInterception(freshDetails, 'customer_conversation_changed_during_processing')
              : 'processing_interrupted';
            await recordJobOutcome({
              jobId: job.job_id,
              ticketId: job.ticket_id,
              generation: job.generation,
              status: 'cancelled',
              startedAt: new Date(startTime),
              finishedAt: new Date(),
              trace: activeTrace,
              delivery: null,
              errorReason: reason,
            });
            await markJobCancelled(job, reason);
            return;
          }
        }
        if (isModelQuotaError(agentErr)) {
          try {
            const pause = await postponeJobForModelQuota(pool, endpointKey, lease, preparation.observedWindowId);
            if (pause.shouldAlert) {
              const oldest = await oldestWaitingTicket(pool).catch(() => undefined);
              await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
                ticketId: job.ticket_id, title: '模型端点额度不足', status: '端点暂缓处理',
                note: [`HTTP 402，连续暂停 ${pause.consecutiveWindows ?? 1} 次。下次尝试时间 ${pause.retryAt?.toISOString()}。`,
                  oldest ? `最久待答工单 #${oldest.ticket_id} 已等待 ${oldest.minutes} 分钟。` : ''].filter(Boolean).join('\n'),
              });
            }
          } catch (error) {
            console.error('[Worker] Could not persist model cooldown or send alert:', error);
          }
          return;
        }
        throw agentErr;
      }

      if (!publicReplyAllowed) {
        const delivery = await deliverInternalDraft({
          pool,
          jobId: job.job_id,
          ticketId: job.ticket_id,
          customerReply: agentResult.customerReply,
          operatorAnswer: agentResult.operatorAnswer,
          baseUrl: ZENDESK_BASE_URL,
          email: ZENDESK_EMAIL,
          apiToken: ZENDESK_API_TOKEN,
        });
        console.log(`[Worker] Operator mention draft delivered as an internal Zendesk note for ticket #${job.ticket_id}; status=${delivery.status}.`);
        await recordJobOutcome({
          jobId: job.job_id,
          ticketId: job.ticket_id,
          generation: job.generation,
          status: 'completed',
          startedAt: new Date(startTime),
          finishedAt: new Date(),
          trace: agentResult.trace,
          delivery,
          internalDraft: formatInternalDraft(agentResult.customerReply, agentResult.operatorAnswer),
        });
        await markJobSucceeded(job);
        return;
      }

      const freshCoverage = await loadCustomerCoverage(pool, job.ticket_id);
      coverage?.pendingCursors.forEach(cursor => {
        if (!freshCoverage.coveredCursors.has(cursor)) freshCoverage.pendingCursors.add(cursor);
      });
      const freshDetails = await getTicketDetails(job.ticket_id, openConnectorService, abortController.signal, freshCoverage);
      if (freshDetails.staffAnsweredCursors?.length) await recordStaffCoverage(pool, job.ticket_id, freshDetails.staffAnsweredCursors);

      // Guard 3: Did customer request human during reasoning?
      if (freshDetails.hasRequestedHuman && !isMentionJob) {
        const humanReason = `模式 B 人工优先保护：最新会话中检测到转人工诉求，旧答复已拦截并移交人工。`;
        if (await handOffToHuman(job, freshDetails, humanReason) === 'superseded') return;
        await recordJobOutcome({
          jobId: job.job_id,
          ticketId: job.ticket_id,
          generation: job.generation,
          status: 'cancelled',
          startedAt: new Date(startTime),
          finishedAt: new Date(),
          trace: agentResult.trace,
          delivery: null,
          errorReason: humanReason,
        });
        await markJobCancelled(job, humanReason);
        return;
      }

      const freshCursors = new Set(freshDetails.readCustomerMessages?.flatMap(message => message.cursors));
      const hasNewMessage =
        freshCoverage.pending.some(({ seq, cursor }) =>
          seq > coverage!.latestSeq && (!cursor || !readCursors.has(cursor))) ||
        [...freshCursors].some(cursor => !readCursors.has(cursor)) ||
        (!freshDetails.isAlreadyAnswered && (
          freshDetails.lastEventId !== initialDetails.lastEventId ||
          freshDetails.customerEventsCount !== initialDetails.customerEventsCount ||
          freshDetails.message.trim() !== initialDetails.message.trim()
        ));

      if (hasNewMessage || freshDetails.isAlreadyAnswered || abortController.signal.aborted) {
        const interceptReason = hasNewMessage
          ? await requeueAfterInterception(freshDetails, 'customer_conversation_changed_during_processing')
          : freshDetails.isAlreadyAnswered ? 'customer_message_already_answered' : 'processing_interrupted';
        await recordJobOutcome({
          jobId: job.job_id,
          ticketId: job.ticket_id,
          generation: job.generation,
          status: 'cancelled',
          startedAt: new Date(startTime),
          finishedAt: new Date(),
          trace: agentResult.trace,
          delivery: null,
          errorReason: interceptReason,
        });

        await markJobCancelled(job, interceptReason);
        return;
      }

      // Internal analysis is retained in run telemetry only. Browser Relay is the sole external write path.
      if (agentResult.internalNote) {
        console.log(`[Worker] Retained internal investigation analysis in telemetry for ticket #${job.ticket_id}; no Zendesk note was written.`);
      }

      const delivered = await deliverWithCustomerCoverage(pool, {
        job, workerId: job.lease_owner, attempt: job.attempts, startingSeq: coverage!.latestSeq,
        messages: initialDetails.readCustomerMessages ?? [], reply: agentResult.reply, signal: abortController.signal,
      }, (deliveryId, reply) => sendBrowserRelay(BROWSER_RELAY_URL, BROWSER_RELAY_KEY, job.ticket_id, reply, {
        deliveryId, signal: AbortSignal.timeout(90_000),
      }));
      if (delivered.outcome === 'uncertain') {
        if (delivered.jobStatus) await recordJobOutcome({
          jobId: job.job_id, ticketId: job.ticket_id, generation: job.generation,
          status: delivered.jobStatus, startedAt: new Date(startTime), finishedAt: new Date(),
          trace: agentResult.trace, errorReason: delivered.reason,
          delivery: { channel: 'browser-relay', status: 'uncertain', delivery_id: delivered.deliveryId },
        });
        return;
      }
      if (delivered.outcome === 'already_sent') {
        await completePreviouslySentPublicJob(pool, job, job.lease_owner, job.attempts);
        return;
      }
      if (delivered.outcome === 'blocked' && await deferUnconfirmedConversation(
        pool, job, job.lease_owner, job.attempts, 'waiting_for_public_delivery_confirmation',
      )) return;
      if (delivered.outcome !== 'delivered') {
        let reason = 'customer_delivery_superseded';
        if (delivered.outcome === 'new_customer_message' || delivered.outcome === 'already_covered') {
          reason = await requeueAfterInterception(freshDetails, delivered.outcome === 'new_customer_message'
            ? 'customer_conversation_changed_before_delivery' : 'customer_coverage_changed_before_delivery');
        }
        if (delivered.outcome === 'unidentified_messages') reason = 'customer_message_identity_unconfirmed';
        await recordJobOutcome({
          jobId: job.job_id, ticketId: job.ticket_id, generation: job.generation,
          status: 'cancelled', startedAt: new Date(startTime), finishedAt: new Date(),
          trace: agentResult.trace, delivery: null, errorReason: reason,
        });
        await markJobCancelled(job, reason);
        return;
      }

      console.log(`[Worker] Confirmed public delivery ${delivered.deliveryId} for ticket #${job.ticket_id}`);
      await recordJobOutcome({
        jobId: job.job_id, ticketId: job.ticket_id, generation: job.generation,
        status: delivered.jobStatus, startedAt: new Date(startTime), finishedAt: new Date(),
        trace: agentResult.trace,
        delivery: { channel: 'browser-relay', status: 'sent', delivery_id: delivered.deliveryId, ...delivered.delivery },
      });

    } catch (runErr: any) {
      console.error(`[Worker] Job ${job.job_id} failed:`, runErr.message);
      await recordJobOutcome({
        jobId: job.job_id,
        ticketId: job.ticket_id,
        generation: job.generation,
        status: 'failed',
        startedAt: new Date(startTime),
        finishedAt: new Date(),
        trace: activeTrace,
        delivery: null,
        errorReason: runErr.message,
      });
      await markJobFailed(job, runErr.message);

      // 🚨 Dispatch Feishu failure alert with draft reply (if generated)
      await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
        title: '🚨 Dify 工单投递/处理失败警告',
        ticketId: job.ticket_id,
        status: '处理/投递失败',
        reason: runErr.message,
        draftReply: activeTrace?.finalReply || undefined,
      });
    } finally {
      clearInterval(monitorTimer);
      clearInterval(leaseTimer);
    }
  }

  let deliveryCheck: Promise<void> | undefined;
  let nextDeliveryCheckAt = 0;
  while (running) {
    try {
      if (!deliveryCheck && Date.now() >= nextDeliveryCheckAt) {
        nextDeliveryCheckAt = Date.now() + 1_500;
        deliveryCheck = reconcilePublicDelivery()
          .catch(error => console.error('[Worker] Public delivery recovery failed:', error))
          .finally(() => { deliveryCheck = undefined; });
      }
      if (activeJobs.size >= MAX_CONCURRENT_JOBS) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }

      const job = await claimNextJob(pool, `${WORKER_ID}:${randomUUID()}`, activeModelClaimDelay);
      if (!running) break;
      if (!job) {
        await new Promise(r => setTimeout(r, 1500));
        continue;
      }

      console.log(`[Worker] Claimed job ${job.job_id} for ticket #${job.ticket_id}`);
      const abortController = new AbortController();
      const jobContext: ActiveJobContext = {
        jobId: job.job_id,
        ticketId: job.ticket_id,
        abortController,
        startedAt: Date.now(),
      };
      activeJobs.set(job.lease_owner, jobContext);

      // Launch job asynchronously in concurrency pool
      jobContext.completion = processJob(job, abortController)
        .catch(err => {
          console.error(`[Worker] Unhandled error processing job ${job.job_id}:`, err);
        })
        .finally(() => {
          activeJobs.delete(job.lease_owner);
        });

    } catch (loopErr: any) {
      console.error('[Worker] Polling loop error:', loopErr.message);
      await new Promise(r => setTimeout(r, 2000));
    }
  }

  stopDirectoryRefresh();
  await Promise.all([stopQueueAlerts(), handoffLoop, deliveryCheck, ...[...activeJobs.values()].map(ctx => ctx.completion)]);
  console.log('[Worker] Shutting down cleanly...');
  server.close();
  await pool.end();
}

export const workerCompletion = main().catch(err => {
  console.error('[Worker] Fatal error:', err);
  process.exit(1);
});
