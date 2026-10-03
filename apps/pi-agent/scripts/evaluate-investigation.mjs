// Isolated, no-delivery model evaluation. Reads the approved test ticket and
// existing configuration; never imports Worker, queue, delivery, sync, or alerts.
import fs from 'node:fs';
import dotenv from 'dotenv';
import pg from 'pg';
import { createOpenConnectorServiceConfig, discoverOpenConnectorTools } from '../dist/openconnector.js';
import { runPiSupportAgent } from '../dist/agent.js';
import { parseModelConfig } from '../dist/model-config.js';
import { getTicketDetails } from '../dist/ticket-context.js';
import { normalizeToolResult } from '../dist/tool-result.js';

const scenario = process.argv[2] || 'education';
const cases = {
  education: '看下这个工单用户的会员是什么会员，是教育优惠还是自己开的，以及这个用户的工作区为什么积分不刷新。',
  product: 'Can a Dify Cloud workflow be published as an MCP server? Explain the supported setup and limitations using current product guidance.',
  bug: 'How can we determine whether a reported Dify Cloud Knowledge Retrieval metadata-filtering bug has a confirmed fix? Check relevant engineering records and explain what is still unknown about rollout.',
};
if (!Object.hasOwn(cases, scenario)) throw new Error('Usage: evaluate-investigation.mjs education|product|bug');
const ticketId = 3726; // Explicitly approved evaluation ticket; no arbitrary customer replay.
const write = process.stdout.write.bind(process.stdout);
// Agent streams model text in normal operation. This diagnostic emits metadata only.
process.stdout.write = () => true;
console.error = () => {};
const report = value => write(`${JSON.stringify(value)}\n`);
const credentials = process.env.CASEWORK_CREDENTIALS_FILE || '/app/credentials.env';
if (fs.existsSync(credentials)) dotenv.config({ path: credentials });
const token = process.env.CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME || process.env.OPENCONNECTOR_RUNTIME_TOKEN;
if (!token) throw new Error('OpenConnector runtime credential is missing');
const connector = createOpenConnectorServiceConfig(
  process.env.CASEWORK_OPENCONNECTOR_MCP_URL || 'https://open-connector.yyg066340.workers.dev/mcp', token,
);
const signal = AbortSignal.timeout(360_000);
if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is required to read the Admin model settings');
const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL.replace(/^postgresql\+\w+:\/\//, 'postgresql://') });
let model;
try { model = parseModelConfig((await pool.query("SELECT value FROM system_settings WHERE key = 'model_config'")).rows[0]?.value); }
finally { await pool.end(); }
const tools = await discoverOpenConnectorTools(connector);
let details;
if (scenario === 'education') details = await getTicketDetails(ticketId, connector, signal);
let lastRound = 0;
let capturedTrace;
try {
  const result = await runPiSupportAgent({
    ticketId, tools, publicReplyAllowed: false, contextPrepared: Boolean(details),
    customerMessage: `${cases[scenario]}\nThis is an isolated read-only evaluation. Return an unsent draft; no external delivery is available.${details ? `\nLatest ticket context:\n${details.message}` : '\nThis product/engineering evaluation question is independent of the test ticket history.'}`,
    requesterEmail: details?.email, attachments: details?.attachments,
    apiKey: model.apiKey,
    modelName: model.model,
    modelBaseUrl: model.baseUrl,
    abortSignal: signal,
    onTrace: trace => { capturedTrace = trace; },
    onRoundTelemetry: rounds => {
      if (rounds.length <= lastRound) return;
      lastRound = rounds.length;
      report({ phase: 'progress', scenario, round: lastRound, tools: rounds.at(-1).toolCalls.map(c => ({ name: c.toolName, status: c.status })) });
    },
  });
  // Trace merge preserves calls removed by compaction, but its array order is
  // not chronological. Use live round order rather than selecting an old plan
  // appended during merge as if it were the final evidence review.
  const order = new Map(result.trace.rounds.flatMap(round => round.toolCalls).map((call, index) => [call.toolCallId, index]));
  const calls = [...result.trace.toolCalls].sort((a, b) => (order.get(a.toolCallId) ?? -1) - (order.get(b.toolCallId) ?? -1));
  const business = calls.filter(call => call.toolName === 'execute_action' && call.arguments?.actionId !== 'custom_mcp.list_tools');
  const reviews = calls.filter(call => call.toolName === 'investigation_check');
  const finalProgress = reviews.length ? normalizeToolResult(reviews.at(-1).result).data : {};
  const actions = business.map(call => ({
    source: call.arguments?.connectionName, action: call.arguments?.input?.toolName || call.arguments?.actionId,
    status: call.outcome?.status,
  }));
  const sourceRead = (source, pattern) => actions.some(call => (call.source === source || call.action?.startsWith(`${source}.`)) && pattern.test(call.action || '') && call.status === 'succeeded');
  const acceptance = {
    englishDraft: Boolean(result.customerReply.trim()) && !/[\u3400-\u9fff]/u.test(result.customerReply),
    noPublicReply: result.reply === '',
    planned: reviews.length > 0,
    reviewed: Array.isArray(finalProgress.gaps) && finalProgress.gaps.length === 0,
    officialBody: sourceRead('dify-docs', /official_(docs|site)_read/),
    feishuBody: sourceRead('feishu', /get_document_content|fetch_document|get_document_blocks/),
    engineeringBody: sourceRead('linear', /get_linear_issue/) || sourceRead('github-mcp', /^github_get_(issue|pr)$/),
  };
  const passed = acceptance.englishDraft && acceptance.noPublicReply && acceptance.planned && acceptance.reviewed &&
    (scenario === 'education' ? acceptance.officialBody && acceptance.feishuBody : scenario === 'product' ? acceptance.officialBody : acceptance.engineeringBody);
  report({ phase: 'summary', scenario, model: result.trace.model, passed, acceptance,
    durationMs: result.trace.totalDurationMs, rounds: result.trace.rounds.length, actions,
    checks: finalProgress.checks?.map(c => ({ id: c.id, source: c.source, phase: c.phase, requestedStatus: c.status,
      linkedCalls: c.callIds?.length ?? 0, next: ['unreviewed', 'candidate_unread', 'failed'].includes(c.phase) ? c.next : undefined })),
    failedCalls: calls.filter(c => c.outcome?.status === 'failed').map(c => ({ tool: c.toolName,
      action: c.arguments?.actionId, connection: c.arguments?.connectionName,
      remote: c.arguments?.input?.toolName,
      category: /permission|forbidden|denied|scope|credential/i.test(c.outcome?.error ?? '') ? 'access' :
        /required|invalid|unknown|not found|parameter|schema/i.test(c.outcome?.error ?? '') ? 'input_or_descriptor' : 'provider_or_transport' })),
    remainingGaps: finalProgress.gaps?.length ?? null, noDelivery: true });
  if (!passed) process.exitCode = 1;
} catch (error) {
  const message = String(error?.message ?? '');
  const category = signal.aborted || /abort|timed?\s*out|timeout/i.test(message) ? 'timeout_or_cancelled' :
    /model request failed/i.test(message) ? 'model_request' : /no text response/i.test(message) ? 'empty_model_response' :
      /English|customer reply|delivery|output/i.test(message) ? 'output_validation' : 'agent_or_transport';
  report({ phase: 'failed', scenario, errorName: error?.name || 'Error', category,
    rounds: capturedTrace?.rounds?.length ?? lastRound, noDelivery: true });
  process.exitCode = 1;
}
