import http from 'node:http';
import pg from 'pg';
import dotenv from 'dotenv';
import { discoverMcpTools, callMcpTool, CustomTool, McpServiceConfig } from './mcp.js';
import { runPiSupportAgent } from './agent.js';
import { sendBrowserRelay } from './relay.js';
import { sendFeishuAlert, HumanAlertTracker } from './alerts.js';

dotenv.config();

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
const FEISHU_WEBHOOK_URL = cleanEnv(process.env.FEISHU_WEBHOOK_URL) || 'https://open.feishu.cn/open-apis/bot/v2/hook/81c40459-690e-49d6-8874-2a89206ec5ea';

const humanAlertTracker = new HumanAlertTracker(30 * 60 * 1000);

async function notifyHumanTakeover(ticketId: number, customerMessage: string, reason: string): Promise<void> {
  if (!humanAlertTracker.shouldAlert(ticketId)) {
    console.log(`[Worker] Human takeover alert for ticket #${ticketId} suppressed (cooldown active).`);
    return;
  }
  humanAlertTracker.recordAlert(ticketId);
  await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
    title: '🙋 Dify 工单转人工接管提醒',
    ticketId,
    status: '模式 B 人工优先锁定（AI 保持静默）',
    customerMessage: customerMessage ? customerMessage.slice(0, 500) : '客户请求人工支持',
    note: reason || '检测到客户诉求转人工或已有真人介入，AI 已自动暂停并移交，请真人客服及时跟进。',
  });
}

if (!DATABASE_URL) {
  console.error('[Worker] Fatal: DATABASE_URL is required');
  process.exit(1);
}

const pool = new pg.Pool({ connectionString: DATABASE_URL });

async function claimNextJob(workerId: string): Promise<any | null> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const selectRes = await client.query(`
      SELECT job_id, ticket_id, session_id, event_id, expected_event_id, generation
      FROM case_jobs
      WHERE status = 'queued' AND available_at <= NOW()
      ORDER BY available_at ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    `);

    if (selectRes.rows.length === 0) {
      await client.query('ROLLBACK');
      return null;
    }

    const job = selectRes.rows[0];
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

async function getActiveModelConfig(): Promise<{
  baseUrl: string;
  model: string;
  apiKey: string;
}> {
  try {
    const res = await pool.query("SELECT value FROM system_settings WHERE key = 'model_config'");
    if (res.rows.length > 0) {
      const cfg = res.rows[0].value;
      if (cfg?.model && cfg?.base_url) {
        return {
          baseUrl: cfg.base_url,
          model: cfg.model,
          apiKey: cfg.api_key || cleanEnv(process.env.CASEWORK_CREDENTIAL_OPENAI_PRIMARY),
        };
      }
    }
  } catch (err: any) {
    console.warn('[Worker] Could not read model_config from system_settings, using env:', err.message);
  }
  return {
    baseUrl: cleanEnv(process.env.CASEWORK_MODEL_BASE_URL) || 'https://api.tokener.dev/v1',
    model: cleanEnv(process.env.CASEWORK_MODEL_NAME) || 'gpt-5.6-terra',
    apiKey: cleanEnv(process.env.CASEWORK_CREDENTIAL_OPENAI_PRIMARY),
  };
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
    delivery: opts.delivery ?? null,
    error: opts.errorReason ?? null,
    total_duration_ms: opts.trace?.totalDurationMs ?? (opts.finishedAt.getTime() - opts.startedAt.getTime()),
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

async function markJobSucceeded(jobId: string) {
  await pool.query(`
    UPDATE case_jobs
    SET status = 'completed',
        finished_at = NOW(),
        updated_at = NOW(),
        lease_owner = NULL,
        lease_expires_at = NULL
    WHERE job_id = $1
  `, [jobId]);
}

async function markJobFailed(jobId: string, reason: string) {
  await pool.query(`
    UPDATE case_jobs
    SET status = 'failed',
        dead_letter_reason = $2,
        finished_at = NOW(),
        updated_at = NOW(),
        lease_owner = NULL,
        lease_expires_at = NULL
    WHERE job_id = $1
  `, [jobId, reason.slice(0, 1024)]);
}

async function markJobCancelled(jobId: string, reason: string) {
  await pool.query(`
    UPDATE case_jobs
    SET status = 'cancelled',
        dead_letter_reason = $2,
        finished_at = NOW(),
        updated_at = NOW(),
        lease_owner = NULL,
        lease_expires_at = NULL
    WHERE job_id = $1
  `, [jobId, reason.slice(0, 1024)]);
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

function extractEventText(ev: any): string {
  if (!ev) return '';
  if (typeof ev.content === 'string' && ev.content.trim()) return ev.content.trim();
  if (ev.content && typeof ev.content === 'object') {
    if (typeof ev.content.text === 'string' && ev.content.text.trim()) return ev.content.text.trim();
    if (typeof ev.content.body === 'string' && ev.content.body.trim()) return ev.content.body.trim();
    if (typeof ev.content.text_fallback === 'string' && ev.content.text_fallback.trim()) return ev.content.text_fallback.trim();
  }
  if (typeof ev.body === 'string' && ev.body.trim()) return ev.body.trim();
  if (typeof ev.message === 'string' && ev.message.trim()) return ev.message.trim();
  if (ev.body && typeof ev.body === 'object' && typeof ev.body.text === 'string' && ev.body.text.trim()) {
    return ev.body.text.trim();
  }
  if (ev.message && typeof ev.message === 'object' && typeof ev.message.text === 'string' && ev.message.text.trim()) {
    return ev.message.text.trim();
  }
  return '';
}

interface TicketDetails {
  message: string;
  email?: string;
  lastEventId?: string;
  lastEventTime?: string;
  customerEventsCount: number;
  hasRequestedHuman: boolean;
  isAlreadyAnswered: boolean;
  unrepliedCount: number;
}

async function getTicketDetails(ticketId: number, zendeskConfig: McpServiceConfig): Promise<TicketDetails> {
  try {
    let email = '';
    let subject = '';

    try {
      const rawTicket = await callMcpTool(
        zendeskConfig.endpoint,
        zendeskConfig.apiKey,
        'get_ticket',
        { ticket_id: ticketId }
      );
      if (rawTicket?.content?.[0]?.text) {
        const parsed = JSON.parse(rawTicket.content[0].text);
        email = parsed?.data?.ticket?.requester?.email || parsed?.data?.ticket?.email || '';
        subject = parsed?.data?.ticket?.subject || '';
      }
    } catch (e: any) {
      console.warn(`[Worker] Could not fetch ticket metadata:`, e.message);
    }

    const allEvents: any[] = [];
    try {
      let afterCursor: string | undefined = undefined;
      let hasMore = true;
      let pageCount = 0;

      while (hasMore && pageCount < 10) {
        pageCount++;
        const args: any = { ticket_id: ticketId, force_refresh: true };
        if (afterCursor) {
          args.conversation_after = afterCursor;
        }

        const convLog = await callMcpTool(
          zendeskConfig.endpoint,
          zendeskConfig.apiKey,
          'get_ticket_conversation_log',
          args
        );

        if (convLog?.content?.[0]?.text) {
          const parsed = JSON.parse(convLog.content[0].text);
          const pageEvents = parsed?.data?.events || [];
          allEvents.push(...pageEvents);
          hasMore = Boolean(parsed?.data?.meta?.has_more);
          afterCursor = parsed?.data?.meta?.after_cursor;
          if (!hasMore || !afterCursor) break;
        } else {
          break;
        }
      }
    } catch (e: any) {
      console.warn(`[Worker] Could not fetch conversation log:`, e.message);
    }

    // Collect unreplied customer messages since the last agent message
    const unrepliedCustomerMessages: Array<{ id: string; text: string; time: string }> = [];
    let hasRequestedHuman = false;
    let isAlreadyAnswered = false;

    // 1. Check if the customer EVER asked for human support in any message
    for (const ev of allEvents) {
      const authorName = ev.author?.display_name || '';
      const authorType = ev.author?.type || '';
      const isAgent = authorType === 'agent' || authorName.includes('David Yang') || authorName.includes('AI Agent');
      if (!isAgent) {
        const text = extractEventText(ev).toLowerCase();
        if (['human', '人工', '真人', '客服'].some(kw => text === kw || text.includes(kw))) {
          hasRequestedHuman = true;
          break;
        }
      }
    }

    // 2. Scan backwards from newest to oldest:
    // If the latest event in the conversation is an agent reply, older customer messages have already been replied!
    let foundAgentReply = false;
    let latestEventId = '';
    let latestEventTime = '';

    for (let i = allEvents.length - 1; i >= 0; i--) {
      const ev = allEvents[i];
      const authorName = ev.author?.display_name || '';
      const authorType = ev.author?.type || '';
      const isAgent = authorType === 'agent' || authorName.includes('David Yang') || authorName.includes('AI Agent');
      const text = extractEventText(ev);

      if (!latestEventId) {
        latestEventId = String(ev.id || ev.reference || '');
        latestEventTime = String(ev.created_at || '');
      }

      if (isAgent) {
        // Encountered the most recent agent reply! Stop collecting customer messages before this reply.
        foundAgentReply = true;
        break;
      } else {
        // Customer message sent after the latest agent reply
        if (text) {
          unrepliedCustomerMessages.unshift({
            id: String(ev.id || ev.reference || ''),
            text: text,
            time: String(ev.created_at || ''),
          });
        }
      }
    }

    // If there are no customer messages after the latest agent reply:
    if (unrepliedCustomerMessages.length === 0 && foundAgentReply) {
      isAlreadyAnswered = true;
    }

    // Combine all unreplied consecutive customer messages (separated by double newline)
    let customerMessage = '';
    if (unrepliedCustomerMessages.length > 0) {
      customerMessage = unrepliedCustomerMessages.map(m => m.text).join('\n\n');
    } else {
      customerMessage = subject || `Ticket #${ticketId}`;
    }

    const lastEventId = unrepliedCustomerMessages.length > 0
      ? unrepliedCustomerMessages[unrepliedCustomerMessages.length - 1].id
      : latestEventId;
    const lastEventTime = unrepliedCustomerMessages.length > 0
      ? unrepliedCustomerMessages[unrepliedCustomerMessages.length - 1].time
      : latestEventTime;

    return {
      message: customerMessage,
      email,
      lastEventId,
      lastEventTime,
      customerEventsCount: unrepliedCustomerMessages.length,
      hasRequestedHuman,
      isAlreadyAnswered,
      unrepliedCount: unrepliedCustomerMessages.length,
    };
  } catch (err: any) {
    console.warn(`[Worker] Error getting ticket details:`, err.message);
    return {
      message: `Ticket #${ticketId}`,
      email: '',
      customerEventsCount: 0,
      hasRequestedHuman: false,
      isAlreadyAnswered: false,
      unrepliedCount: 0,
    };
  }
}

// =========================================================================
// Mention Poller: Scan for @David Yang internal notes
// =========================================================================
const processedMentions = new Set<string>();
const ticketUpdateCache = new Map<number, string>();

async function initMentionTracker() {
  try {
    const res = await pool.query("SELECT event_id FROM events WHERE source = 'zendesk_internal_mention'");
    for (const row of res.rows) {
      const cid = String(row.event_id).replace('mention:', '');
      processedMentions.add(cid);
    }
    console.log(`[Mention Poller] Initialized tracker with ${processedMentions.size} existing processed mentions.`);
  } catch (err: any) {
    console.warn(`[Mention Poller] Could not initialize tracker from DB:`, err.message);
  }
}

async function scanInternalMentions(zendeskService: McpServiceConfig) {
  try {
    const searchRes = await callMcpTool(
      zendeskService.endpoint,
      zendeskService.apiKey,
      'search_tickets',
      { query: 'type:ticket status<solved order_by:updated_at sort:desc' }
    );
    if (!searchRes?.content?.[0]?.text) return;
    const parsed = JSON.parse(searchRes.content[0].text);
    const tickets = parsed?.data?.results || [];

    for (const t of tickets.slice(0, 15)) {
      const tid = Number(t.id);
      const updatedAt = String(t.updated_at || '');
      if (ticketUpdateCache.get(tid) === updatedAt) {
        continue;
      }
      ticketUpdateCache.set(tid, updatedAt);

      // Fetch recent comments
      const commRes = await callMcpTool(
        zendeskService.endpoint,
        zendeskService.apiKey,
        'get_ticket_comments',
        { ticket_id: tid }
      );
      if (!commRes?.content?.[0]?.text) continue;
      const commParsed = JSON.parse(commRes.content[0].text);
      const comments = commParsed?.data?.comments || [];

      for (const c of comments.slice(-5)) {
        const cid = String(c.id);
        if (processedMentions.has(cid)) continue;

        // Must be internal note (public: false)
        if (c.public !== false) continue;

        const body = String(c.body || '').trim();
        if (!body) continue;

        // Skip AI bot signatures
        if (body.includes('Dify AI Agent') || body.includes('AI Agent is now processing')) {
          continue;
        }

        const lower = body.toLowerCase();
        const mentionTriggers = [
          '@david yang',
          '@davidyang',
          '**david yang**',
          '@agent',
          '@ai agent',
          '@ai',
          '@dify-agent',
          '@saas-agent',
        ];
        const matchedTrigger = mentionTriggers.find(tr => lower.includes(tr));
        if (!matchedTrigger) continue;

        console.log(`[Mention Poller] 🔔 Detected internal note @David Yang mention on ticket #${tid} (comment ${cid}): "${body.slice(0, 100)}"`);
        processedMentions.add(cid);

        // 1. Clear human takeover lock ("不受人工限制")
        await pool.query(`
          UPDATE ticket_states
          SET human_cancelled_at = NULL,
              updated_at = NOW()
          WHERE ticket_id = $1
        `, [tid]);
        humanAlertTracker.clear(tid);

        // 2. Persist in events table for deduplication
        await pool.query(`
          INSERT INTO events (
            event_id, ticket_id, occurred_at, source, customer_origin, event_type, message_id, payload, received_at
          ) VALUES (
            $1, $2, NOW(), 'zendesk_internal_mention', 'operator', 'ticket.mention', $3, $4, NOW()
          ) ON CONFLICT (event_id) DO NOTHING
        `, [
          `mention:${cid}`,
          tid,
          cid,
          JSON.stringify({ comment_id: cid, body, trigger: matchedTrigger })
        ]);

        // 3. Enqueue job in case_jobs
        const jobId = `job-mention-${cid}`;
        const sessionId = `session-mention-${cid}`;
        const eventId = `mention:${cid}`;

        await pool.query(`
          INSERT INTO case_jobs (
            job_id, session_id, event_id, expected_event_id, ticket_id, generation,
            status, attempts, available_at, created_at, updated_at,
            config_revision, root_model_config_id, child_model_config_ids,
            model_digest, skill_digest, tool_digest
          )
          VALUES (
            $1, $2, $3, $4, $5, 1,
            'queued', 0, NOW(), NOW(), NOW(),
            'rev-1', 'gpt-5.6-terra', '[]', 'digest-model', 'digest-skill', 'digest-tool'
          )
          ON CONFLICT (ticket_id) WHERE status = 'queued'
          DO UPDATE SET
            job_id = $1,
            session_id = $2,
            event_id = $3,
            expected_event_id = $4,
            available_at = NOW(),
            updated_at = NOW()
        `, [jobId, sessionId, eventId, eventId, tid]);

        console.log(`[Mention Poller] 🚀 Enqueued active mention job ${jobId} for ticket #${tid}. Human restriction unlocked!`);
      }
    }
  } catch (err: any) {
    console.warn(`[Mention Poller] Scan error:`, err.message);
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

  // Discover all 6 MCP services
  const mcpServices: McpServiceConfig[] = [
    {
      name: 'db-gateway',
      endpoint: cleanEnv(process.env.DB_GATEWAY_MCP_URL) || 'https://zendesk.smlershou.top/db-gateway/mcp',
      apiKey: cleanEnv(process.env.CASEWORK_CREDENTIAL_DB_GATEWAY),
    },
    {
      name: 'zendesk',
      endpoint: cleanEnv(process.env.ZENDESK_MCP_URL) || 'https://zendesk.smlershou.top/zendesk-mcp/mcp',
      apiKey: cleanEnv(process.env.CASEWORK_CREDENTIAL_ZENDESK_MCP),
      excludedTools: ['add_internal_note', 'update_ticket_status', 'update_ticket_metadata'],
    },
    {
      name: 'signoz',
      endpoint: cleanEnv(process.env.SIGNOZ_MCP_URL) || 'https://zendesk.smlershou.top/signoz-mcp/mcp',
      apiKey: cleanEnv(process.env.CASEWORK_CREDENTIAL_SIGNOZ_MCP),
    },
    {
      name: 'source',
      endpoint: cleanEnv(process.env.SOURCE_MCP_URL) || 'https://zendesk.smlershou.top/source-mcp/mcp',
      apiKey: cleanEnv(process.env.CASEWORK_CREDENTIAL_SOURCE_MCP),
    },
    {
      name: 'github',
      endpoint: cleanEnv(process.env.GITHUB_MCP_URL) || 'https://zendesk.smlershou.top/github-mcp/mcp',
      apiKey: cleanEnv(process.env.CASEWORK_CREDENTIAL_GITHUB_MCP),
    },
    {
      name: 'docs',
      endpoint: cleanEnv(process.env.DOCS_MCP_URL) || 'https://zendesk.smlershou.top/docs-tool-service/mcp',
      apiKey: cleanEnv(process.env.CASEWORK_CREDENTIAL_DOCS_MCP),
    },
  ];

  let allTools: CustomTool[] = [];
  for (const s of mcpServices) {
    if (s.apiKey) {
      const tools = await discoverMcpTools(s);
      allTools.push(...tools);
    } else {
      console.warn(`[Worker] Skipping MCP ${s.name}: No API key provided`);
    }
  }
  console.log(`[Worker] Loaded total ${allTools.length} tools across MCP services`);

  const zendeskService = mcpServices.find(s => s.name === 'zendesk')!;

  // Main polling loop
  let running = true;
  process.on('SIGINT', () => { running = false; });
  process.on('SIGTERM', () => { running = false; });

  // Start background mention poller (checks every 5 seconds)
  await initMentionTracker();
  (async () => {
    while (running) {
      try {
        await scanInternalMentions(zendeskService);
      } catch (e: any) {
        console.warn(`[Mention Poller] Loop error:`, e.message);
      }
      await new Promise(r => setTimeout(r, 5000));
    }
  })();

  while (running) {
    try {
      const job = await claimNextJob(WORKER_ID);
      if (!job) {
        await new Promise(r => setTimeout(r, 2000));
        continue;
      }

      console.log(`[Worker] Claimed job ${job.job_id} for ticket #${job.ticket_id}`);
      const startTime = Date.now();

      const isMentionJob = Boolean(
        job.event_id?.startsWith('mention:') ||
        job.expected_event_id?.startsWith('mention:')
      );

      // Guard 1: Has this ticket already been transferred to human in database?
      // NOTE: Mention jobs BYPASS this guard because an operator explicitly tagged @David Yang!
      const isHumanInDb = await isTicketHumanCancelled(job.ticket_id);
      if (isHumanInDb && !isMentionJob) {
        const humanReason = `模式 B 人工优先保护：该工单此前已转人工客服，AI 坚决不介入。`;
        console.warn(`[Worker] 🛡️ HUMAN TAKEOVER: ${humanReason}`);
        if (humanAlertTracker.shouldAlert(job.ticket_id)) {
          const details = await getTicketDetails(job.ticket_id, zendeskService);
          await notifyHumanTakeover(job.ticket_id, details.message, humanReason);
        }
        await markJobCancelled(job.job_id, humanReason);
        continue;
      }

      let activeTrace: any = null;
      try {
        const initialDetails = await getTicketDetails(job.ticket_id, zendeskService);

        // Guard 0: Has this ticket already been answered by an agent reply?
        // (Prevent duplicate reply when customer sends consecutive messages and agent already replied!)
        if (initialDetails.isAlreadyAnswered && !isMentionJob) {
          const skipReason = `防重复回复保护：该工单最新消息已由座席/AI完成回复（无待回复客户新消息），自动跳过防止二次抢答。`;
          console.log(`[Worker] ⏭️ SKIP: ${skipReason}`);
          await markJobCancelled(job.job_id, skipReason);
          continue;
        }

        // Guard 2: Did the customer EVER ask for human in this conversation thread?
        // NOTE: Mention jobs BYPASS this guard as well!
        if (initialDetails.hasRequestedHuman && !isMentionJob) {
          const humanReason = `模式 B 人工优先保护：对话历史中检测到客户曾请求人工客服，AI 保持静默。`;
          console.warn(`[Worker] 🛡️ HUMAN TAKEOVER: ${humanReason}`);
          try {
            await pool.query(
              `INSERT INTO ticket_states (ticket_id, human_cancelled_at, updated_at)
               VALUES ($1, NOW(), NOW())
               ON CONFLICT (ticket_id) DO UPDATE SET human_cancelled_at = NOW(), updated_at = NOW()`,
              [job.ticket_id]
            );
          } catch (e: any) {
            console.warn('[Worker] Failed to update ticket_states for human cancellation:', e.message);
          }
          await notifyHumanTakeover(job.ticket_id, initialDetails.message, humanReason);
          await markJobCancelled(job.job_id, humanReason);
          continue;
        }

        // If this is a mention job, extract the operator's note instructions
        let effectiveCustomerMessage = initialDetails.message;
        if (isMentionJob) {
          try {
            const evRes = await pool.query(
              'SELECT payload FROM events WHERE event_id = $1 LIMIT 1',
              [job.expected_event_id || job.event_id]
            );
            const operatorInstruction = evRes.rows[0]?.payload?.body;
            if (operatorInstruction) {
              effectiveCustomerMessage = `[来自人工座席内部备注 @David Yang 的直接指令]:\n"${operatorInstruction}"\n\n[工单客户最新消息]:\n"${initialDetails.message}"`;
              console.log(`[Worker] 📌 Injected operator internal mention instruction for ticket #${job.ticket_id}: "${operatorInstruction.slice(0, 80)}"`);
            }
          } catch (e: any) {
            console.warn(`[Worker] Could not read mention payload:`, e.message);
          }
        }

        const modelConfig = await getActiveModelConfig();
        console.log(`[Worker] Running Pi Agent for ticket #${job.ticket_id} (Customer: ${initialDetails.email || 'unknown'}, Model: ${modelConfig.model}${isMentionJob ? ', Trigger: @David Yang Mention' : ''}): "${effectiveCustomerMessage.slice(0, 80)}..."`);

        const agentResult = await runPiSupportAgent({
          ticketId: job.ticket_id,
          customerMessage: effectiveCustomerMessage,
          requesterEmail: initialDetails.email,
          tools: allTools,
          apiKey: modelConfig.apiKey,
          modelBaseUrl: modelConfig.baseUrl,
          modelName: modelConfig.model,
        });

        activeTrace = agentResult.trace;
        console.log(`[Worker] Pi Agent completed reasoning for ticket #${job.ticket_id} in ${Date.now() - startTime}ms (${agentResult.trace.toolCalls.length} tool calls).`);

        // =========================================================================
        // Pre-Delivery Freshness Check (生成/投递前重新拉取工单内容，检查用户是否发了新消息)
        // =========================================================================
        console.log(`[Worker] Pre-delivery check: Fetching latest conversation for ticket #${job.ticket_id} to verify no new customer messages...`);
        const freshDetails = await getTicketDetails(job.ticket_id, zendeskService);

        // Guard 3: Did customer request human during reasoning?
        if (freshDetails.hasRequestedHuman && !isMentionJob) {
          const humanReason = `模式 B 人工优先保护：最新会话中检测到转人工诉求，旧答复已拦截并移交人工。`;
          console.warn(`[Worker] 🛡️ HUMAN TAKEOVER: ${humanReason}`);
          try {
            await pool.query(
              `INSERT INTO ticket_states (ticket_id, human_cancelled_at, updated_at)
               VALUES ($1, NOW(), NOW())
               ON CONFLICT (ticket_id) DO UPDATE SET human_cancelled_at = NOW(), updated_at = NOW()`,
              [job.ticket_id]
            );
          } catch (e: any) {
            console.warn('[Worker] Failed to update ticket_states for human cancellation:', e.message);
          }
          await notifyHumanTakeover(job.ticket_id, freshDetails.message, humanReason);
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
          await markJobCancelled(job.job_id, humanReason);
          continue;
        }

        // Guard 4: Did customer send a new message during reasoning? (Skip if isMentionJob)
        const hasNewMessage =
          !isMentionJob && (
            (freshDetails.lastEventId && initialDetails.lastEventId && freshDetails.lastEventId !== initialDetails.lastEventId) ||
            (freshDetails.customerEventsCount > initialDetails.customerEventsCount) ||
            (freshDetails.message.trim() !== initialDetails.message.trim())
          );

        if (hasNewMessage) {
          const interceptReason = `检测到客户在推理期间发送了新消息（“${freshDetails.message.slice(0, 60)}...”）。旧答复已自动拦截作废，触发 10 秒防抖并重新排队最新会话。`;
          console.warn(`[Worker] 🚨 INTERCEPTED: ${interceptReason}`);

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

          // Re-queue ticket with 10s debounce
          await pool.query(`
            INSERT INTO case_jobs (
              job_id, session_id, event_id, expected_event_id, ticket_id, generation,
              status, attempts, available_at, created_at, updated_at,
              config_revision, root_model_config_id, child_model_config_ids,
              model_digest, skill_digest, tool_digest
            )
            VALUES (
              $1, $2, $3, $4, $5, $6,
              'queued', 0, NOW() + INTERVAL '10 seconds', NOW(), NOW(),
              'rev-1', 'gpt-5.6-terra', '[]', 'digest-model', 'digest-skill', 'digest-tool'
            )
            ON CONFLICT (ticket_id) WHERE status = 'queued'
            DO UPDATE SET
              available_at = NOW() + INTERVAL '10 seconds',
              updated_at = NOW()
          `, [
            `job-${Date.now()}-${job.ticket_id}`,
            `session-retry-${Date.now()}-${job.ticket_id}`,
            freshDetails.lastEventId || `event-${Date.now()}`,
            freshDetails.lastEventId || `event-${Date.now()}`,
            job.ticket_id,
            job.generation,
          ]);

          await markJobCancelled(job.job_id, interceptReason);
          continue;
        }

        console.log(`[Worker] Pre-delivery check passed. Delivering via Browser Relay...`);
        const relayResult = await sendBrowserRelay(
          BROWSER_RELAY_URL,
          BROWSER_RELAY_KEY,
          job.ticket_id,
          agentResult.reply
        );

        if (relayResult.status !== 'sent') {
          const relayErrMsg = relayResult.error || `Browser relay returned unexpected status: ${relayResult.status}`;
          await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
            title: '🚨 Dify 工单投递失败警告',
            ticketId: job.ticket_id,
            status: `投递异常 (${relayResult.status})`,
            reason: relayErrMsg,
            draftReply: agentResult.reply,
          });
          throw new Error(relayErrMsg);
        }

        console.log(`[Worker] Browser Relay delivered successfully for ticket #${job.ticket_id}: status=${relayResult.status} (relayMs=${relayResult.total_duration_ms ?? 0})`);

        await recordJobOutcome({
          jobId: job.job_id,
          ticketId: job.ticket_id,
          generation: job.generation,
          status: 'completed',
          startedAt: new Date(startTime),
          finishedAt: new Date(),
          trace: agentResult.trace,
          delivery: {
            channel: 'browser-relay',
            status: relayResult.status,
            total_duration_ms: relayResult.total_duration_ms,
            timings: relayResult.timings || { total_ms: relayResult.total_duration_ms },
          },
        });

        await markJobSucceeded(job.job_id);

        // Automatically drain/cancel any redundant queued jobs for this ticket to prevent duplicate replies!
        try {
          const drainRes = await pool.query(`
            UPDATE case_jobs
            SET status = 'cancelled',
                dead_letter_reason = 'coalesced_into_previous_delivery',
                finished_at = NOW(),
                updated_at = NOW()
            WHERE ticket_id = $1
              AND status = 'queued'
              AND job_id != $2
          `, [job.ticket_id, job.job_id]);
          if ((drainRes.rowCount ?? 0) > 0) {
            console.log(`[Worker] 🧹 Cancelled ${drainRes.rowCount} redundant queued jobs for ticket #${job.ticket_id} to prevent duplicate reply.`);
          }
        } catch (e: any) {
          console.warn(`[Worker] Failed to drain redundant queued jobs:`, e.message);
        }
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
        await markJobFailed(job.job_id, runErr.message);

        // 🚨 Dispatch Feishu failure alert with draft reply (if generated)
        await sendFeishuAlert(FEISHU_WEBHOOK_URL, {
          title: '🚨 Dify 工单投递/处理失败警告',
          ticketId: job.ticket_id,
          status: '处理/投递失败',
          reason: runErr.message,
          draftReply: activeTrace?.finalReply || undefined,
        });
      }
    } catch (loopErr: any) {
      console.error('[Worker] Polling loop error:', loopErr.message);
      await new Promise(r => setTimeout(r, 3000));
    }
  }

  console.log('[Worker] Shutting down cleanly...');
  server.close();
  await pool.end();
}

main().catch(err => {
  console.error('[Worker] Fatal error:', err);
  process.exit(1);
});
