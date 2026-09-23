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
const READ_TOOL_DIRECTORY_NAME = 'list_read_tools';
const BLOCKED_SERVICES = new Set(['gmail']);
const CUSTOM_MCP_TOOL_CACHE_TTL_MS = 60_000;
const READ_TOOL_DIRECTORY_TTL_MS = 5 * 60_000;
const READ_TOOL_PAGE_SIZE = 50;
// OpenConnector resolves live capabilities for every action in a search page;
// keep each Worker request bounded while still following every page.
const ACTION_SEARCH_PAGE_SIZE = 50;
const customMcpToolCache = new WeakMap<
  McpServiceConfig,
  Map<string, { expiresAt: number; tools: Promise<any[]> }>
>();
type ReadToolEntry = {
  service: string;
  connectionName: string;
  actionId: string;
  toolName?: string;
  description: string;
  inputSummary?: unknown;
};
const readToolDirectoryCache = new WeakMap<
  McpServiceConfig,
  Map<string, { expiresAt: number; entries: Promise<ReadToolEntry[]> }>
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

function assertConnectionAllowed(connectionName: string): void {
  if (connectionName.toLowerCase().includes('gmail')) {
    throw new Error(`OpenConnector connection ${connectionName} is disabled for Zendesk Agent investigations`);
  }
}

export function validateOpenConnectorControlCall(
  toolName: string,
  params: Record<string, any>,
): void {
  if (!OPENCONNECTOR_TOOL_SET.has(toolName)) {
    throw new Error(`OpenConnector tool ${toolName} is not exposed to the Zendesk Agent`);
  }

  if (toolName === 'list_connections') {
    if (params.service !== undefined) assertServiceAllowed(requiredString(params.service, 'service filter'));
    return;
  }
  if (toolName === 'search_actions') {
    assertServiceAllowed(requiredString(params.service, 'service filter'));
    return;
  }

  const actionId = requiredString(params.actionId, 'actionId');
  assertServiceAllowed(actionService(actionId));
  assertConnectionAllowed(requiredString(params.connectionName, 'connectionName'));
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

// Catch a common model-generated SQL typo before it becomes an opaque
// provider_error. This is a syntax check, not a replacement for the DB
// Gateway's read-only SQL validation.
export function assertDbGatewaySqlHasClosedStrings(sql: unknown): void {
  if (typeof sql !== 'string') return;
  let state: 'normal' | 'string' | 'identifier' | 'line-comment' | 'block-comment' | 'dollar' = 'normal';
  let dollarTag = '';
  let escapedString = false;
  for (let index = 0; index < sql.length; index++) {
    const char = sql[index];
    const next = sql[index + 1];
    if (state === 'line-comment') {
      if (char === '\n') state = 'normal';
    } else if (state === 'block-comment') {
      if (char === '*' && next === '/') { state = 'normal'; index++; }
    } else if (state === 'dollar') {
      if (sql.startsWith(dollarTag, index)) { state = 'normal'; index += dollarTag.length - 1; }
    } else if (state === 'identifier') {
      if (char === '"' && next === '"') index++;
      else if (char === '"') state = 'normal';
    } else if (state === 'string') {
      if (escapedString && char === '\\' && next !== undefined) index++;
      else if (char === "'" && next === "'") index++;
      else if (char === "'") state = 'normal';
    } else if (char === '-' && next === '-') {
      state = 'line-comment'; index++;
    } else if (char === '/' && next === '*') {
      state = 'block-comment'; index++;
    } else if (char === '"') {
      state = 'identifier';
    } else if (char === "'") {
      escapedString = sql[index - 1]?.toLowerCase() === 'e' && !/[A-Za-z_0-9]/.test(sql[index - 2] ?? '');
      state = 'string';
    } else if (char === '$') {
      const match = /^\$[A-Za-z_][A-Za-z_0-9]*\$|^\$\$/.exec(sql.slice(index));
      if (match) { dollarTag = match[0]; state = 'dollar'; index += dollarTag.length - 1; }
    }
  }
  if (state === 'string') {
    throw new Error('db_gateway_query SQL has an unterminated quoted string. Correct the SQL and retry; no database request was sent.');
  }
}

export async function callOpenConnectorTool(
  config: McpServiceConfig,
  toolName: string,
  params: Record<string, any>,
  signal?: AbortSignal,
): Promise<any> {
  validateOpenConnectorControlCall(toolName, params);
  let requestParams = params;

  if (toolName === 'execute_action') {
    const actionId = requiredString(params.actionId, 'actionId');
    const connectionName = requiredString(params.connectionName, 'connectionName');
    let input = asRecord(params.input) ?? {};

    if (actionId === 'custom_mcp.call_tool') {
      if (Object.hasOwn(input, 'confirmOperation')) {
        throw new Error('OpenConnector confirmOperation is forbidden for Zendesk Agent investigations');
      }
      // Pi can echo the qualified MCP catalog name. OpenConnector expects the
      // bare remote name; still re-check that exact tool's live read policy.
      const suppliedToolName = requiredString(input.toolName, 'custom MCP toolName');
      const remoteToolName = suppliedToolName.startsWith('custom_mcp.remote.')
        ? suppliedToolName.slice('custom_mcp.remote.'.length)
        : suppliedToolName;
      if (remoteToolName !== suppliedToolName) {
        input = { ...input, toolName: remoteToolName };
        requestParams = { ...params, input };
      }
      const descriptor = await assertCustomMcpToolIsReadOnly(config, connectionName, remoteToolName, signal);
      validateCustomMcpPagination(input, descriptor.pagination);
      if (connectionName === 'dify-db' && remoteToolName === 'db_gateway_query') {
        assertDbGatewaySqlHasClosedStrings(asRecord(input.arguments)?.sql);
      }
    } else if (actionId !== 'custom_mcp.list_tools') {
      await assertNativeActionIsReadOnly(config, actionId, connectionName, signal);
    }
  }

  const result = await readWithRetry(boundedSignal => callMcpTool(config.endpoint, config.apiKey, toolName, requestParams, boundedSignal, config.protocolVersion), signal);
  if (toolName !== 'list_connections' || params.service !== undefined) return result;
  const payload = parseJsonTextResult(result);
  if (payload.ok !== true || !Array.isArray(payload.data)) return result;
  const safePayload = {
    ...payload,
    data: payload.data.filter((connection: any) =>
      typeof connection?.service === 'string' &&
      typeof connection?.connectionName === 'string' &&
      !BLOCKED_SERVICES.has(connection.service.toLowerCase()) &&
      !connection.connectionName.toLowerCase().includes('gmail'),
    ),
  };
  return {
    ...result,
    structuredContent: safePayload,
    content: [{ type: 'text', text: JSON.stringify(safePayload) }],
  };
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
  const controls = tools.filter((tool) => OPENCONNECTOR_TOOL_SET.has(tool.name)).map(tool => tool.name !== 'execute_action' ? tool : {
    ...tool,
    description: `${tool.description}\nFor custom_mcp.call_tool, continue an OpenConnector nextCursor via input.pagination={mode:"single",cursor:nextCursor,pageSize:25}. Never put an ocmcp1. cursor inside input.arguments. Keep the same tool and query. Native Zendesk after_cursor is a different cursor. Use get_ticket for metadata only, and get_ticket_conversation_log for conversation pages.`,
  });
  if (!OPENCONNECTOR_AGENT_TOOLS.every(name => controls.some(tool => tool.name === name))) {
    throw new Error('OpenConnector did not expose all required investigation controls');
  }
  return [...controls, createReadToolDirectoryTool(config)];
}

async function loadReadToolDirectory(
  config: McpServiceConfig,
  service?: string,
  connectionName?: string,
  signal?: AbortSignal,
): Promise<ReadToolEntry[]> {
  let cache = readToolDirectoryCache.get(config);
  if (!cache) {
    cache = new Map();
    readToolDirectoryCache.set(config, cache);
  }
  const cacheKey = JSON.stringify([service ?? null, connectionName ?? null]);
  const now = Date.now();
  const cached = cache.get(cacheKey);
  if (cached && cached.expiresAt > now) return cached.entries;

  const entries = (async () => {
    const connectionResult = await callOpenConnectorTool(config, 'list_connections', service ? { service } : {}, signal);
    const connections = parseOpenConnectorResult(connectionResult).data;
    if (!Array.isArray(connections)) throw new Error('OpenConnector did not return a connection list');
    const allowedConnections = connections.filter((connection: any) =>
      typeof connection?.service === 'string' &&
      typeof connection?.connectionName === 'string' &&
      (!connectionName || connection.connectionName === connectionName) &&
      !connection.connectionName.toLowerCase().includes('gmail') &&
      !BLOCKED_SERVICES.has(connection.service.toLowerCase()),
    );
    const entries: ReadToolEntry[] = [];
    const nativeServices = new Map<string, string[]>();
    for (const connection of allowedConnections) {
      if (connection.service === 'custom_mcp') {
        const result = await callOpenConnectorTool(config, 'execute_action', {
          actionId: 'custom_mcp.list_tools',
          connectionName: connection.connectionName,
          input: {},
        }, signal);
        const descriptors = payloadData(result).tools;
        if (!Array.isArray(descriptors)) throw new Error(`OpenConnector custom MCP ${connection.connectionName} did not return a tool list`);
        for (const descriptor of descriptors) {
          if (descriptor?.operationType !== 'read' || descriptor.confirmationRequired === true || typeof descriptor.name !== 'string') continue;
          entries.push({
            service: 'custom_mcp',
            connectionName: connection.connectionName,
            actionId: 'custom_mcp.call_tool',
            toolName: descriptor.name,
            description: typeof descriptor.description === 'string' ? descriptor.description : '',
            inputSummary: descriptor.inputSchema,
          });
        }
      } else {
        const names = nativeServices.get(connection.service) ?? [];
        names.push(connection.connectionName);
        nativeServices.set(connection.service, names);
      }
    }
    for (const [service, connectionNames] of nativeServices) {
      let cursor: string | undefined;
      const seenCursors = new Set<string>();
      do {
        const result = await callOpenConnectorTool(config, 'search_actions', {
          service,
          limit: ACTION_SEARCH_PAGE_SIZE,
          ...(cursor ? { cursor } : {}),
        }, signal);
        const payload = parseOpenConnectorResult(result);
        if (!Array.isArray(payload.data)) throw new Error(`OpenConnector ${service} did not return an action list`);
        for (const action of payload.data) {
          if (action?.operationType !== 'read' || action?.capability?.policy?.allowed === false || typeof action.id !== 'string') continue;
          for (const connectionName of connectionNames) {
            entries.push({
              service,
              connectionName,
              actionId: action.id,
              description: typeof action.description === 'string' ? action.description : '',
              inputSummary: action.inputSummary,
            });
          }
        }
        const nextCursor = payload.page?.nextCursor;
        if (nextCursor != null && (typeof nextCursor !== 'string' || seenCursors.has(nextCursor))) {
          throw new Error(`OpenConnector ${service} returned an invalid action cursor`);
        }
        if (nextCursor) seenCursors.add(nextCursor);
        cursor = nextCursor || undefined;
      } while (cursor);
    }
    return entries.sort((a, b) =>
      `${a.service}/${a.connectionName}/${a.toolName || a.actionId}`.localeCompare(`${b.service}/${b.connectionName}/${b.toolName || b.actionId}`),
    );
  })();
  cache.set(cacheKey, { expiresAt: now + READ_TOOL_DIRECTORY_TTL_MS, entries });
  entries.catch(() => {
    if (cache?.get(cacheKey)?.entries === entries) cache.delete(cacheKey);
  });
  return entries;
}

function createReadToolDirectoryTool(config: McpServiceConfig): CustomTool {
  return {
    name: READ_TOOL_DIRECTORY_NAME,
    label: READ_TOOL_DIRECTORY_NAME,
    description: 'List every currently connected OpenConnector read-only business tool, including native provider actions and Custom MCP tools. Filter by service or connectionName, and page through the full directory. A listed tool is a discovery candidate; execution still checks live permissions and read-only classification.',
    promptSnippet: 'Discover all connected read-only OpenConnector business tools.',
    parameters: {
      type: 'object',
      properties: {
        service: { type: 'string', description: 'Optional provider service, for example feishu or custom_mcp.' },
        connectionName: { type: 'string', description: 'Optional connection name, for example dify-docs.' },
        offset: { type: 'integer', minimum: 0, description: 'Offset from the previous page.' },
      },
    },
    execute: async (_toolCallId, params, signal) => {
      try {
        const input = asRecord(params) ?? {};
        const service = input.service === undefined ? undefined : requiredString(input.service, 'service filter');
        if (service) assertServiceAllowed(service);
        const connectionName = input.connectionName === undefined ? undefined : requiredString(input.connectionName, 'connectionName');
        if (connectionName) assertConnectionAllowed(connectionName);
        const offset = input.offset === undefined ? 0 : input.offset;
        if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('OpenConnector directory offset must be a non-negative integer');
        const allEntries = await loadReadToolDirectory(config, service, connectionName, signal);
        const matching = allEntries.filter(entry =>
          (!service || entry.service === service) && (!connectionName || entry.connectionName === connectionName),
        );
        const page = {
          total: matching.length,
          offset,
          nextOffset: offset + READ_TOOL_PAGE_SIZE < matching.length ? offset + READ_TOOL_PAGE_SIZE : null,
          tools: matching.slice(offset, offset + READ_TOOL_PAGE_SIZE),
        };
        return { content: [{ type: 'text', text: JSON.stringify(page) }], details: page };
      } catch (error: any) {
        const message = error?.name === 'AbortError'
          ? 'OpenConnector read-tool discovery was cancelled'
          : 'OpenConnector read-tool discovery failed; retry with a service filter or use the scoped discovery controls';
        return { content: [{ type: 'text', text: `Tool error: ${message}` }], isError: true };
      }
    },
  };
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
  if (toolName === READ_TOOL_DIRECTORY_NAME) {
    const service = asRecord(args)?.service;
    return typeof service === 'string' ? service : 'openconnector';
  }
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
