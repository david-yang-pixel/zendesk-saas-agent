import http from 'node:http';
import pg from 'pg';
import dotenv from 'dotenv';
import { discoverMcpTools, callMcpTool, CustomTool, McpServiceConfig } from './mcp.js';
import { runPiSupportAgent } from './agent.js';
import { sendBrowserRelay } from './relay.js';

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

    let customerMessage = '';
    let lastEventId = '';
    let lastEventTime = '';
    let customerEventsCount = 0;
    let hasRequestedHuman = false;

    // 1. Check if the customer EVER asked for human support in the thread
    for (const ev of allEvents) {
      const authorName = ev.author?.display_name || '';
      const authorType = ev.author?.type || '';
      if (authorName.includes('David Yang') || authorName.includes('AI Agent') || authorType === 'agent') {
        continue;
      }
      const text = extractEventText(ev).toLowerCase();
      if (['human', '人工', '真人', '客服'].some(kw => text === kw || text.includes(kw))) {
        hasRequestedHuman = true;
        break;
      }
    }

    // 2. Scan backwards to extract the LATEST customer message
    for (let i = allEvents.length - 1; i >= 0; i--) {
      const ev = allEvents[i];
      const authorName = ev.author?.display_name || '';
      const authorType = ev.author?.type || '';
      if (authorName.includes('David Yang') || authorName.includes('AI Agent') || authorType === 'agent') {
        continue;
      }
      customerEventsCount++;
      if (!lastEventId) {
        lastEventId = String(ev.id || ev.reference || '');
        lastEventTime = String(ev.created_at || '');
      }
      const text = extractEventText(ev);
      if (!customerMessage && text) {
        customerMessage = text;
      }
    }

    if (!customerMessage) {
      customerMessage = subject || `Ticket #${ticketId}`;
    }

    return {
      message: customerMessage,
      email,
      lastEventId,
      lastEventTime,
      customerEventsCount,
      hasRequestedHuman,
    };
  } catch (err: any) {
    console.warn(`[Worker] Error getting ticket details:`, err.message);
    return { message: `Ticket #${ticketId}`, email: '', customerEventsCount: 0, hasRequestedHuman: false };
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

  while (running) {
    try {
      const job = await claimNextJob(WORKER_ID);
      if (!job) {
        await new Promise(r => setTimeout(r, 2000));
        continue;
      }

      console.log(`[Worker] Claimed job ${job.job_id} for ticket #${job.ticket_id}`);
      const startTime = Date.now();

      // Guard 1: Has this ticket already been transferred to human in database?
      const isHumanInDb = await isTicketHumanCancelled(job.ticket_id);
      if (isHumanInDb) {
        const humanReason = `模式 B 人工优先保护：该工单此前已转人工客服，AI 坚决不介入。`;
        console.warn(`[Worker] 🛡️ HUMAN TAKEOVER: ${humanReason}`);
        await markJobCancelled(job.job_id, humanReason);
        continue;
      }

      let activeTrace: any = null;
      try {
        const initialDetails = await getTicketDetails(job.ticket_id, zendeskService);

        // Guard 2: Did the customer EVER ask for human in this conversation thread?
        if (initialDetails.hasRequestedHuman) {
          const humanReason = `模式 B 人工优先保护：对话历史中检测到客户曾请求人工客服，AI 保持静默。`;
          console.warn(`[Worker] 🛡️ HUMAN TAKEOVER: ${humanReason}`);
          await markJobCancelled(job.job_id, humanReason);
          continue;
        }

        const modelConfig = await getActiveModelConfig();
        console.log(`[Worker] Running Pi Agent for ticket #${job.ticket_id} (Customer: ${initialDetails.email || 'unknown'}, Model: ${modelConfig.model}): "${initialDetails.message.slice(0, 80)}..."`);

        const agentResult = await runPiSupportAgent({
          ticketId: job.ticket_id,
          customerMessage: initialDetails.message,
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
        if (freshDetails.hasRequestedHuman) {
          const humanReason = `模式 B 人工优先保护：最新会话中检测到转人工诉求，旧答复已拦截并移交人工。`;
          console.warn(`[Worker] 🛡️ HUMAN TAKEOVER: ${humanReason}`);
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

        // Guard 4: Did customer send a new message during reasoning?
        const hasNewMessage =
          (freshDetails.lastEventId && initialDetails.lastEventId && freshDetails.lastEventId !== initialDetails.lastEventId) ||
          (freshDetails.customerEventsCount > initialDetails.customerEventsCount) ||
          (freshDetails.message.trim() !== initialDetails.message.trim());

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
