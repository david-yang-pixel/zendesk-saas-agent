import {
  callMcpTool,
  discoverMcpTools,
  type CustomTool,
  type McpServiceConfig,
} from './mcp.js';
import { setTimeout as delay } from 'node:timers/promises';

// Only used on controls and operations that have passed the read-only guard.
async function readWithRetry<T>(read: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted();
    const timeout = AbortSignal.timeout(45_000);
    const boundedSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
    try {
      return await read(boundedSignal);
    } catch (error: any) {
      if (signal?.aborted || error?.retryable === false) throw error;
      const transient = error?.name === 'TimeoutError' ||
        /MCP returned an empty response|fetch failed|call failed \((429|502|503|504)\)/.test(error?.message || '');
      if (!transient || attempt >= 2) throw error;
      await delay(500 * 2 ** attempt, undefined, { signal });
    }
  }
}

export function parseOpenConnectorResult(result: any): Record<string, any> {
  const payload = parseJsonTextResult(result);
  if (result?.isError || payload.ok === false || payload.success === false) {
    throw new Error(`OpenConnector tool failed: ${payload.error?.message || payload.message || JSON.stringify(payload.error || payload)}`);
  }
  return payload;
}

export const OPENCONNECTOR_AGENT_TOOLS = [
  'list_connections',
  'search_actions',
  'get_action_guide',
  'execute_action',
] as const;

const OPENCONNECTOR_TOOL_SET = new Set<string>(OPENCONNECTOR_AGENT_TOOLS);
const BLOCKED_SERVICES = new Set(['gmail']);
const CUSTOM_MCP_TOOL_CACHE_TTL_MS = 60_000;
const customMcpToolCache = new WeakMap<
  McpServiceConfig,
  Map<string, { expiresAt: number; tools: Promise<any[]> }>
>();

const CUSTOM_MCP_SERVICE_NAMES: Record<string, string> = {
  zendesk: 'zendesk',
  'dify-db': 'db-gateway',
  'dify-signoz': 'signoz',
  'dify-source': 'source',
  'github-mcp': 'github',
  'dify-docs': 'docs',
};

function asRecord(value: unknown): Record<string, any> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, any>
    : undefined;
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`OpenConnector ${name} is required`);
  }
  return value.trim();
}

function actionService(actionId: string): string {
  const dot = actionId.indexOf('.');
  return dot === -1 ? actionId : actionId.slice(0, dot);
}

function assertServiceAllowed(service: string): void {
  if (BLOCKED_SERVICES.has(service.toLowerCase())) {
    throw new Error(`OpenConnector service ${service} is disabled for Zendesk Agent investigations`);
  }
}

export function validateOpenConnectorControlCall(
  toolName: string,
  params: Record<string, any>,
): void {
  if (!OPENCONNECTOR_TOOL_SET.has(toolName)) {
    throw new Error(`OpenConnector tool ${toolName} is not exposed to the Zendesk Agent`);
  }

  if (toolName === 'list_connections' || toolName === 'search_actions') {
    const service = requiredString(params.service, 'service filter');
    assertServiceAllowed(service);
    return;
  }

  const actionId = requiredString(params.actionId, 'actionId');
  assertServiceAllowed(actionService(actionId));
  requiredString(params.connectionName, 'connectionName');
}

function parseJsonTextResult(result: any): Record<string, any> {
  const structured = asRecord(result?.structuredContent);
  if (structured) return structured;

  const content = Array.isArray(result?.content) ? result.content : [];
  for (const block of content) {
    if (block?.type !== 'text' || typeof block.text !== 'string') continue;
    try {
      const parsed = asRecord(JSON.parse(block.text));
      if (parsed) return parsed;
    } catch {
      continue;
    }
  }
  throw new Error('OpenConnector returned an unreadable control response');
}

function payloadData(result: any): Record<string, any> {
  const payload = parseJsonTextResult(result);
  if (payload.ok !== true) {
    const message = payload.error?.message || 'OpenConnector control request failed';
    throw new Error(String(message));
  }
  const data = asRecord(payload.data);
  if (!data) throw new Error('OpenConnector control response did not include data');
  return data;
}

async function callRawOpenConnectorAction(
  config: McpServiceConfig,
  actionId: string,
  connectionName: string,
  input: Record<string, any>,
  signal?: AbortSignal,
): Promise<any> {
  return readWithRetry(boundedSignal => callMcpTool(
    config.endpoint,
    config.apiKey,
    'execute_action',
    { actionId, connectionName, input },
    boundedSignal,
    config.protocolVersion,
  ), signal);
}

async function assertNativeActionIsReadOnly(
  config: McpServiceConfig,
  actionId: string,
  connectionName: string,
  signal?: AbortSignal,
): Promise<void> {
  const guide = await readWithRetry(boundedSignal => callMcpTool(
    config.endpoint,
    config.apiKey,
    'get_action_guide',
    { actionId, connectionName },
    boundedSignal,
    config.protocolVersion,
  ), signal);
  const operationType = payloadData(guide).capability?.operationType;
  if (operationType !== 'read') {
    throw new Error(`OpenConnector action ${actionId} is ${String(operationType || 'unclassified')}; only read actions are allowed`);
  }
}

async function assertCustomMcpToolIsReadOnly(
  config: McpServiceConfig,
  connectionName: string,
  toolName: string,
  signal?: AbortSignal,
): Promise<Record<string, any>> {
  let cache = customMcpToolCache.get(config);
  if (!cache) {
    cache = new Map();
    customMcpToolCache.set(config, cache);
  }
  const now = Date.now();
  let cached = cache.get(connectionName);
  if (!cached || cached.expiresAt <= now) {
    const tools = callRawOpenConnectorAction(
      config,
      'custom_mcp.list_tools',
      connectionName,
      {},
      signal,
    ).then((listResult) => {
      const discovered = payloadData(listResult).tools;
      if (!Array.isArray(discovered)) {
        throw new Error(`OpenConnector custom MCP ${connectionName} did not return a tool list`);
      }
      return discovered;
    });
    cached = { expiresAt: now + CUSTOM_MCP_TOOL_CACHE_TTL_MS, tools };
    cache.set(connectionName, cached);
    tools.catch(() => {
      if (cache?.get(connectionName)?.tools === tools) cache.delete(connectionName);
    });
  }
  const tools = await cached.tools;
  const descriptor = tools.find((tool: any) => tool?.name === toolName);
  if (!descriptor) {
    throw new Error(`OpenConnector custom MCP tool not found: ${connectionName}/${toolName}`);
  }
  if (descriptor.operationType !== 'read' || descriptor.confirmationRequired === true) {
    const githubHint = connectionName === 'github-mcp'
      ? ' Configure this verified GitHub read tool as read in the OpenConnector connection toolOperationTypes.'
      : '';
    throw new Error(
      `OpenConnector custom MCP tool ${connectionName}/${toolName} is ${String(descriptor.operationType || 'unclassified')}; only read tools are allowed.${githubHint}`,
    );
  }
  return descriptor;
}

export function validateCustomMcpPagination(input: Record<string, any>, rule?: Record<string, any>): void {
  const args = asRecord(input.arguments) ?? {};
  const pagination = asRecord(input.pagination);
  const cursorField = rule?.requestCursorField;
  const remoteCursorFields = new Set(['conversation_after', ...(cursorField ? [cursorField] : [])]);
  for (const field of remoteCursorFields) {
    if (typeof args[field] === 'string' && args[field].startsWith('ocmcp1.')) {
      throw new Error(`OpenConnector wrapped cursor belongs in input.pagination.cursor, not input.arguments.${field}. Use pagination: { mode: "single", cursor: nextCursor, pageSize: 25 } with the same tool and query.`);
    }
    if (pagination?.cursor != null && args[field] != null) {
      throw new Error('Specify either input.pagination.cursor or the native cursor, never both');
    }
  }
  if (pagination?.cursor != null && (typeof pagination.cursor !== 'string' || !pagination.cursor.startsWith('ocmcp1.'))) {
    throw new Error('input.pagination.cursor requires the opaque OpenConnector nextCursor (ocmcp1.); native after_cursor belongs in the remote arguments');
  }
}

export async function callOpenConnectorTool(
  config: McpServiceConfig,
  toolName: string,
  params: Record<string, any>,
  signal?: AbortSignal,
): Promise<any> {
  validateOpenConnectorControlCall(toolName, params);

  if (toolName === 'execute_action') {
    const actionId = requiredString(params.actionId, 'actionId');
    const connectionName = requiredString(params.connectionName, 'connectionName');
    const input = asRecord(params.input) ?? {};

    if (actionId === 'custom_mcp.call_tool') {
      if (Object.hasOwn(input, 'confirmOperation')) {
        throw new Error('OpenConnector confirmOperation is forbidden for Zendesk Agent investigations');
      }
      const remoteToolName = requiredString(input.toolName, 'custom MCP toolName');
      const descriptor = await assertCustomMcpToolIsReadOnly(config, connectionName, remoteToolName, signal);
      validateCustomMcpPagination(input, descriptor.pagination);
    } else if (actionId !== 'custom_mcp.list_tools') {
      await assertNativeActionIsReadOnly(config, actionId, connectionName, signal);
    }
  }

  return readWithRetry(boundedSignal => callMcpTool(config.endpoint, config.apiKey, toolName, params, boundedSignal, config.protocolVersion), signal);
}

export function createOpenConnectorServiceConfig(endpoint: string, apiKey: string): McpServiceConfig {
  const config: McpServiceConfig = {
    name: 'openconnector',
    endpoint,
    apiKey,
    excludedTools: ['list_apps'],
    // The legacy SSE fallback can close before its terminal result. Use the
    // server's per-request JSON protocol, including its required metadata.
    protocolVersion: '2026-07-28',
  };
  config.executeTool = (toolName, params, signal) => callOpenConnectorTool(config, toolName, params, signal);
  return config;
}

export async function discoverOpenConnectorTools(config: McpServiceConfig): Promise<CustomTool[]> {
  const tools = await discoverMcpTools(config);
  return tools.filter((tool) => OPENCONNECTOR_TOOL_SET.has(tool.name)).map(tool => tool.name !== 'execute_action' ? tool : {
    ...tool,
    description: `${tool.description}\nFor custom_mcp.call_tool, continue an OpenConnector nextCursor via input.pagination={mode:"single",cursor:nextCursor,pageSize:25}. Never put an ocmcp1. cursor inside input.arguments. Keep the same tool and query. Native Zendesk after_cursor is a different cursor. Use get_ticket for metadata only, and get_ticket_conversation_log for conversation pages.`,
  });
}

export async function callOpenConnectorCustomMcpTool(
  config: McpServiceConfig,
  connectionName: string,
  toolName: string,
  args: Record<string, any>,
  signal?: AbortSignal,
): Promise<any> {
  return callOpenConnectorTool(
    config,
    'execute_action',
    {
      actionId: 'custom_mcp.call_tool',
      connectionName,
      input: { toolName, arguments: args },
    },
    signal,
  );
}

export function inferOpenConnectorService(toolName: string, args?: unknown): string | undefined {
  if (!OPENCONNECTOR_TOOL_SET.has(toolName)) return undefined;
  const input = asRecord(args) ?? {};
  if (toolName === 'list_connections' || toolName === 'search_actions') {
    return typeof input.service === 'string' ? input.service : 'openconnector';
  }
  const actionId = typeof input.actionId === 'string' ? input.actionId : '';
  if (actionService(actionId) === 'custom_mcp') {
    const connectionName = typeof input.connectionName === 'string' ? input.connectionName : '';
    return CUSTOM_MCP_SERVICE_NAMES[connectionName] || 'custom-mcp';
  }
  return actionId ? actionService(actionId) : 'openconnector';
}
