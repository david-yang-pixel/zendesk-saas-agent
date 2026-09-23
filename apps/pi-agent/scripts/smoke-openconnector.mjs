// Read-only Pi tool-path smoke test. Prints only tool names, counts, and status.
// It never starts a Worker, posts to Zendesk, or outputs tool payloads/secrets.
import { existsSync } from 'node:fs';
import dotenv from 'dotenv';
import { createOpenConnectorServiceConfig, discoverOpenConnectorTools } from '../dist/openconnector.js';
import { normalizeToolResult } from '../dist/tool-result.js';

const ticketId = Number(process.argv[2]);
if (!Number.isSafeInteger(ticketId) || ticketId <= 0) {
  throw new Error('Usage: node scripts/smoke-openconnector.mjs <approved-test-ticket-id>');
}
const credentialsPath = process.env.CASEWORK_CREDENTIALS_FILE || '/app/credentials.env';
if (existsSync(credentialsPath)) dotenv.config({ path: credentialsPath });
const token = process.env.CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME || process.env.OPENCONNECTOR_RUNTIME_TOKEN;
if (!token) throw new Error('OpenConnector runtime token is not configured');

const config = createOpenConnectorServiceConfig(
  process.env.CASEWORK_OPENCONNECTOR_MCP_URL || 'https://open-connector.yyg066340.workers.dev/mcp',
  token,
);
const tools = await discoverOpenConnectorTools(config);
const byName = (name) => {
  const tool = tools.find(item => item.name === name);
  if (!tool) throw new Error(`Pi Agent control ${name} is missing`);
  return tool;
};
const connectionsTool = byName('list_connections');
const directoryTool = byName('list_read_tools');
const executeTool = byName('execute_action');
const payloadOf = (result) => result.details?.structuredContent ||
  JSON.parse(result.details?.content?.find(block => block.type === 'text')?.text || '{}');
const connectionResult = await connectionsTool.execute('smoke-connections', {});
if (connectionResult.isError) throw new Error('Pi Agent cannot list OpenConnector connections');
const connections = payloadOf(connectionResult).data;
if (!Array.isArray(connections) || connections.some(item =>
  item.service === 'gmail' || item.connectionName?.toLowerCase().includes('gmail'))) {
  throw new Error('OpenConnector returned an invalid or Gmail-visible connection list');
}

let directoryFailures = 0;
for (const { service, connectionName } of connections) {
  const entries = [];
  let failed = false;
  let offset = 0;
  do {
    const page = await directoryTool.execute('smoke-directory', { service, connectionName, offset }, AbortSignal.timeout(150_000));
    if (page.isError) { failed = true; break; }
    entries.push(...page.details.tools);
    offset = page.details.nextOffset;
  } while (offset !== null);
  console.log(JSON.stringify({ phase: 'directory', service, connectionName,
    readTools: entries.length, status: !failed && entries.length > 0 ? 'passed' : 'failed' }));
  if (failed || entries.length === 0) directoryFailures++;
}

let executionFailures = 0;
async function executeRead(service, connectionName, actionId, input, toolName) {
  const started = Date.now();
  try {
    const result = await executeTool.execute('smoke-read', {
      actionId, connectionName,
      input: toolName ? { toolName, arguments: input } : input,
    }, AbortSignal.timeout(150_000));
    const status = normalizeToolResult(result, result.isError === true).status;
    console.log(JSON.stringify({ phase: 'execute', service, connectionName,
      tool: toolName || actionId, status, elapsedMs: Date.now() - started }));
    if (status !== 'succeeded') executionFailures++;
    return status === 'succeeded' ? payloadOf(result).data : undefined;
  } catch (error) {
    executionFailures++;
    console.log(JSON.stringify({ phase: 'execute', service, connectionName,
      tool: toolName || actionId, status: 'failed', errorName: error?.name || 'Error' }));
    return undefined;
  }
}
const custom = (connectionName, toolName, input) =>
  executeRead('custom_mcp', connectionName, 'custom_mcp.call_tool', input, toolName);

await custom('dify-db', 'db_gateway_query', { database: 'prod', ticket_id: String(ticketId), sql: 'SELECT 1 AS healthcheck' });
await custom('dify-db', 'db_gateway_query', { database: 'billing', ticket_id: String(ticketId), sql: 'SELECT 1 AS healthcheck' });
await custom('dify-docs', 'official_docs_search', { query: 'education credits', limit: 1 });
await custom('dify-signoz', 'signoz_list_services', { limit: 1 });
await custom('dify-source', 'repo_status', {});
const repo = 'langgenius/dify';
await custom('github-mcp', 'github_code_search', { query: `repo:${repo} Workflow`, limit: 1 });
const prs = await custom('github-mcp', 'github_search_prs', { repo, query: 'fix', limit: 1 });
if (prs?.items?.[0]?.number) await custom('github-mcp', 'github_get_pr', { repo, pr_number: prs.items[0].number });
else { executionFailures++; console.log(JSON.stringify({ phase: 'execute', service: 'custom_mcp', connectionName: 'github-mcp', tool: 'github_get_pr', status: 'not-tested-no-search-result' })); }
const issues = await custom('github-mcp', 'github_search_issues', { repo, query: 'bug', limit: 1 });
if (issues?.items?.[0]?.number) await custom('github-mcp', 'github_get_issue', { repo, issue_number: issues.items[0].number });
else { executionFailures++; console.log(JSON.stringify({ phase: 'execute', service: 'custom_mcp', connectionName: 'github-mcp', tool: 'github_get_issue', status: 'not-tested-no-search-result' })); }
await custom('zendesk', 'get_ticket', { ticket_id: ticketId, include_comments: false, include_conversation_log: false });
await executeRead('feishu', 'default', 'feishu.search_documents', { query: 'education credits', pageSize: 1 });
await executeRead('linear', 'default', 'linear.list_linear_teams', {});
await executeRead('notion', 'default', 'notion.search', { query: 'Dify', pageSize: 1 });
await executeRead('cloudflare_mcp', 'default', 'cloudflare_mcp.docs', { query: 'Workers 1102 resource limits' });

console.log(JSON.stringify({ phase: 'summary', connections: connections.length,
  directoryFailures, executionFailures, noDelivery: true }));
if (directoryFailures || executionFailures) process.exitCode = 1;
