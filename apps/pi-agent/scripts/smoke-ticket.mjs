// Read-only production diagnostic. No worker, queue, relay, or alert imports.
import fs from 'node:fs';
import pg from 'pg';
import { getTicketDetails } from '../dist/ticket-context.js';
import { createOpenConnectorServiceConfig, discoverOpenConnectorTools } from '../dist/openconnector.js';
import { runPiSupportAgent } from '../dist/agent.js';

const ticketId = Number(process.argv[2]);
if (!Number.isSafeInteger(ticketId) || ticketId <= 0) throw new Error('Usage: node scripts/smoke-ticket.mjs <ticket-id> [--model]');
for (const line of fs.readFileSync('/app/credentials.env', 'utf8').split('\n')) {
  const match = line.match(/^([A-Z_]+)=(.*)$/);
  if (match) process.env[match[1]] = match[2].replace(/^["']|["']$/g, '');
}
const connector = createOpenConnectorServiceConfig(
  process.env.CASEWORK_OPENCONNECTOR_MCP_URL || 'https://open-connector.yyg066340.workers.dev/mcp',
  process.env.CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME || process.env.OPENCONNECTOR_RUNTIME_TOKEN,
);
const signal = AbortSignal.timeout(240_000);
const details = await getTicketDetails(ticketId, connector, signal);
console.log(JSON.stringify({ phase: 'context', ticketId, unrepliedCount: details.unrepliedCount,
  lastEventId: details.lastEventId, isAlreadyAnswered: details.isAlreadyAnswered,
  messageLength: details.message.length, noDelivery: true }));
if (process.argv.includes('--model')) {
  const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL.replace(/^postgresql\+[a-zA-Z0-9_]+:\/\//, 'postgresql://') });
  let model;
  try {
    model = (await pool.query("SELECT value FROM system_settings WHERE key = 'model_config'")).rows[0]?.value;
  } finally { await pool.end(); }
  const tools = await discoverOpenConnectorTools(connector);
  if (tools.length !== 4) throw new Error(`Expected four read-only controls, got ${tools.length}`);
  const result = await runPiSupportAgent({
    ticketId, requesterEmail: details.email, attachments: details.attachments,
    customerMessage: `内部只读诊断，不发送、不修改任何数据。请必须调用 execute_action，actionId=custom_mcp.call_tool，connectionName=zendesk，input={"toolName":"get_ticket","arguments":{"ticket_id":${ticketId}}} 核对当前工单，再根据以下最新客户消息给出简短未发送草稿：\n${details.message}`,
    tools, apiKey: model?.api_key || process.env.CASEWORK_CREDENTIAL_OPENAI_PRIMARY,
    modelBaseUrl: model?.base_url || process.env.CASEWORK_MODEL_BASE_URL || 'https://api.tokener.dev/v1',
    modelName: model?.model || process.env.CASEWORK_MODEL_NAME || 'gpt-5.6-terra', abortSignal: signal,
  });
  const calls = result.trace.toolCalls;
  if (!calls.length || !result.trace.finalReply?.trim()) throw new Error('Smoke requires actual tool execution and a nonempty final reply');
  const ticketRead = calls.find(call => call.toolName === 'execute_action' && call.arguments?.input?.toolName === 'get_ticket');
  const readResult = ticketRead?.result?.details;
  if (!ticketRead || ticketRead.status === 'failed' || ticketRead.result?.isError || !readResult || readResult.isError || readResult.error) throw new Error('Model did not complete the required ticket read');
  console.log(JSON.stringify({ phase: 'model', toolCalls: calls.map(call => ({ name: call.toolName, isError: call.result?.isError })), replyLength: result.trace.finalReply.length, noDelivery: true }));
}
