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
// Refresh before the TTL ends so no investigation waits on a cold directory.
const READ_TOOL_DIRECTORY_REFRESH_MS = 4 * 60_000;
const FULL_DIRECTORY_KEY = JSON.stringify([null, null]);
// Per-connection budget in the prompt; list_read_tools pages the remainder.
const READ_TOOL_DIRECTORY_LINE_LIMIT = 700;
const READ_TOOL_PAGE_SIZE = 50;
const DISCOVERY_DESCRIPTION_LIMIT = 220;
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

function shortDescription(value: unknown): string {
  return typeof value === 'string' ? value.slice(0, DISCOVERY_DESCRIPTION_LIMIT) : '';
}

function agentVisibleResult(result: any, payload: Record<string, any>): any {
  // Replace both MCP representations: retaining the original content would
  // still feed the large provider profile back into the model context.
  return {
    ...(result?.isError === true ? { isError: true } : {}),
    structuredContent: payload,
    content: [{ type: 'text', text: JSON.stringify(payload) }],
  };
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
  // Optional filters can arrive as empty strings/null from strict model schemas.
  // Treat them as omission, as documented, rather than an invalid service.
  if (toolName === 'list_connections' && (params.service == null ||
      (typeof params.service === 'string' && !params.service.trim()))) {
    const { service: _service, ...unfiltered } = params;
    params = unfiltered;
  }
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
      const rawToolName = input.toolName ?? input.tool ?? input.name;
      const suppliedToolName = requiredString(rawToolName, 'custom MCP toolName');
      const remoteToolName = suppliedToolName.startsWith('custom_mcp.remote.')
        ? suppliedToolName.slice('custom_mcp.remote.'.length)
        : suppliedToolName;
      let normalizedArguments = asRecord(input.arguments);
      if (!normalizedArguments) {
        const { toolName: _tn, tool: _t, name: _n, pagination: _p, ...inlineArgs } = input;
        if (Object.keys(inlineArgs).length > 0) {
          normalizedArguments = inlineArgs;
        } else {
          normalizedArguments = {};
        }
      }
      input = { ...input, toolName: remoteToolName, arguments: normalizedArguments };
      delete (input as any).tool;
      delete (input as any).name;
      requestParams = { ...params, input };

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
  if (toolName === 'get_action_guide') {
    const payload = parseJsonTextResult(result);
    const data = asRecord(payload.data);
    if (payload.ok !== true || !data) return result;
    const capability = asRecord(data.capability) ?? {};
    const markdown = typeof data.markdown === 'string'
      ? data.markdown.replace(/## Current Connection[\s\S]*?(?=\n## Notes For Agents|$)/, '## Current Connection\nConnection and policy are checked at execution time.\n')
      : undefined;
    return agentVisibleResult(result, { ok: true, data: {
      capability: { operationType: capability.operationType, policy: { allowed: capability.policy?.allowed } },
      ...(markdown ? { markdown } : {}),
    } });
  }
  if (toolName === 'execute_action' && params.actionId === 'custom_mcp.list_tools') {
    const payload = parseJsonTextResult(result);
    const data = asRecord(payload.data);
    if (payload.ok !== true || !Array.isArray(data?.tools)) return result;
    return agentVisibleResult(result, { ok: true, data: { tools: data.tools
      .filter((tool: any) => tool?.operationType === 'read' && tool?.confirmationRequired !== true)
      .map((tool: any) => ({
        name: tool.name,
        description: shortDescription(tool.description),
        operationType: tool.operationType,
        confirmationRequired: false,
        inputSchema: tool.inputSchema,
        pagination: tool.pagination,
      })) } });
  }
  if (toolName !== 'list_connections' && toolName !== 'search_actions') return result;
  const payload = parseJsonTextResult(result);
  if (payload.ok !== true || !Array.isArray(payload.data)) return result;
  if (toolName === 'list_connections') {
    return agentVisibleResult(result, {
      ok: true,
      data: payload.data.filter((connection: any) =>
        typeof connection?.service === 'string' &&
        typeof connection?.connectionName === 'string' &&
        !BLOCKED_SERVICES.has(connection.service.toLowerCase()) &&
        !connection.connectionName.toLowerCase().includes('gmail'),
      ).map((connection: any) => ({
        service: connection.service,
        connectionName: connection.connectionName,
      })),
    });
  }
  return agentVisibleResult(result, {
    ok: true,
    data: payload.data.map((action: any) => ({
      id: action?.id,
      name: action?.name,
      description: shortDescription(action?.description),
      operationType: action?.operationType,
      ...(action?.capability?.policy?.allowed === false ? { allowed: false } : {}),
    })),
    page: payload.page,
  });
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

/** Scope successful discovery responses to the investigation that requested them. */
export function withRunDiscoveryCache(tool: CustomTool): CustomTool {
  if (!['list_connections', 'get_action_guide', 'search_actions', READ_TOOL_DIRECTORY_NAME].includes(tool.name)) return tool;
  const results = new Map<string, Awaited<ReturnType<CustomTool['execute']>>>();
  return {
    ...tool,
    async execute(id, params, signal) {
      signal?.throwIfAborted();
      // Discovery controls take flat JSON parameters, including pagination cursors.
      const key = JSON.stringify(Object.entries(params ?? {}).sort(([a], [b]) => a.localeCompare(b)));
      if (results.has(key)) return structuredClone(results.get(key));
      const result = await tool.execute(id, params, signal);
      if (!signal?.aborted && result && !result.isError) results.set(key, structuredClone(result));
      return result;
    },
  };
}

export async function discoverOpenConnectorTools(config: McpServiceConfig): Promise<CustomTool[]> {
  const tools = await discoverMcpTools(config);
  const controls = tools.filter((tool) => OPENCONNECTOR_TOOL_SET.has(tool.name)).map(tool => {
    // The upstream API permits implicit defaults; this Agent deliberately
    // requires an explicit source. Expose the same contract to the model.
    const required = tool.name === 'search_actions' ? ['service'] :
      ['get_action_guide', 'execute_action'].includes(tool.name) ? ['actionId', 'connectionName'] : [];
    const properties = { ...tool.parameters.properties };
    for (const field of required) properties[field] = {
      ...properties[field], type: 'string', minLength: 1,
      ...(field === 'connectionName' ? { description: 'Exact connectionName returned by list_connections/list_read_tools, including "default" when that is the selected connection. Required by this Agent.' } : {}),
    };
    return {
      ...tool,
      parameters: { ...tool.parameters, properties, required: [...new Set([...(tool.parameters.required ?? []), ...required])] },
      description: tool.name !== 'execute_action' ? tool.description : `${tool.description}\nFor custom_mcp.call_tool, input format is strictly: { "toolName": "<remote_tool_name>", "arguments": { ... } } (never use "tool" or "name"). Continue an OpenConnector nextCursor via input.pagination={mode:"single",cursor:nextCursor,pageSize:25}. Never put an ocmcp1. cursor inside input.arguments. Keep the same tool and query. Native Zendesk after_cursor is a different cursor. Use get_ticket for metadata only, and get_ticket_conversation_log for conversation pages.`,
    };
  });
  if (!OPENCONNECTOR_AGENT_TOOLS.every(name => controls.some(tool => tool.name === name))) {
    throw new Error('OpenConnector did not expose all required investigation controls');
  }
  return [...controls, createReadToolDirectoryTool(config)];
}

function directoryCache(config: McpServiceConfig) {
  let cache = readToolDirectoryCache.get(config);
  if (!cache) {
    cache = new Map();
    readToolDirectoryCache.set(config, cache);
  }
  return cache;
}

async function loadReadToolDirectory(
  config: McpServiceConfig,
  service?: string,
  connectionName?: string,
  signal?: AbortSignal,
): Promise<ReadToolEntry[]> {
  const cache = directoryCache(config);
  const now = Date.now();
  // A fresh full directory answers every scoped query; the caller applies the scope.
  const full = cache.get(FULL_DIRECTORY_KEY);
  if (full && full.expiresAt > now) return full.entries;
  const cacheKey = JSON.stringify([service ?? null, connectionName ?? null]);
  const cached = cache.get(cacheKey);
  if (cached && cached.expiresAt > now) return cached.entries;

  const entries = fetchReadToolDirectory(config, service, connectionName, signal);
  cache.set(cacheKey, { expiresAt: now + READ_TOOL_DIRECTORY_TTL_MS, entries });
  entries.catch(() => {
    if (cache.get(cacheKey)?.entries === entries) cache.delete(cacheKey);
  });
  return entries;
}

/**
 * Reload the full directory. Only a complete successful load replaces the
 * cached copy, so a failed refresh keeps serving the last good directory.
 */
export async function refreshReadToolDirectory(config: McpServiceConfig, signal?: AbortSignal): Promise<ReadToolEntry[]> {
  const entries = await fetchReadToolDirectory(config, undefined, undefined, signal);
  directoryCache(config).set(FULL_DIRECTORY_KEY, {
    expiresAt: Date.now() + READ_TOOL_DIRECTORY_TTL_MS, entries: Promise.resolve(entries),
  });
  return entries;
}

/** Prewarm the directory now and keep it fresh in the background. Returns a stop function. */
export function startReadToolDirectoryRefresh(
  config: McpServiceConfig,
  intervalMs = READ_TOOL_DIRECTORY_REFRESH_MS,
): () => void {
  let inFlight: Promise<unknown> | undefined;
  const refresh = () => {
    if (inFlight) return;
    inFlight = refreshReadToolDirectory(config)
      .catch((error: any) => console.warn('[OpenConnector] Read-tool directory refresh failed; keeping the previous directory:', error?.message ?? error))
      .finally(() => { inFlight = undefined; });
  };
  refresh();
  const timer = setInterval(refresh, intervalMs);
  timer.unref?.();
  return () => clearInterval(timer);
}

function directoryLine(prefix: string, names: readonly string[]): string {
  let line = prefix;
  let shown = 0;
  for (const name of names) {
    const next = `${shown ? ', ' : ''}${name}`;
    if (line.length + next.length > READ_TOOL_DIRECTORY_LINE_LIMIT) break;
    line += next;
    shown++;
  }
  return shown < names.length ? `${line}${shown ? ', ' : ''}… +${names.length - shown} more (list_read_tools)` : line;
}

/**
 * A compact view of the cached full directory for the model's prompt, so it
 * can pick sources without discovery calls. Never contacts OpenConnector: a
 * cold or failed directory yields nothing and the model discovers as before.
 */
export async function describeReadToolDirectory(config: McpServiceConfig): Promise<string | undefined> {
  const full = readToolDirectoryCache.get(config)?.get(FULL_DIRECTORY_KEY);
  if (!full || full.expiresAt <= Date.now()) return undefined;
  let entries: ReadToolEntry[];
  try {
    entries = await full.entries;
  } catch {
    return undefined;
  }
  const customTools = new Map<string, string[]>();
  const native = new Map<string, { connections: Set<string>; actions: Set<string> }>();
  for (const entry of entries) {
    if (entry.service === 'custom_mcp' && entry.toolName) {
      customTools.set(entry.connectionName, [...customTools.get(entry.connectionName) ?? [], entry.toolName]);
    } else if (entry.service !== 'custom_mcp') {
      const provider = native.get(entry.service) ?? { connections: new Set(), actions: new Set() };
      provider.connections.add(entry.connectionName);
      provider.actions.add(entry.actionId);
      native.set(entry.service, provider);
    }
  }
  if (!customTools.size && !native.size) return undefined;
  const lines = [
    'Connected read-only OpenConnector sources (live discovery snapshot from the Worker). Choose sources from this list instead of calling list_connections or paging list_read_tools; each call is still checked against its live read classification and permissions when it executes.',
  ];
  if (customTools.size) {
    lines.push('Custom MCP: execute_action with actionId "custom_mcp.call_tool", the connectionName below, and input {"toolName": "<tool>", "arguments": {...}}. Call custom_mcp.list_tools for a connection only when you need a tool\'s input schema.');
    for (const [connectionName, tools] of customTools) lines.push(directoryLine(`- ${connectionName}: `, tools));
  }
  if (native.size) {
    lines.push('Native providers: execute_action with the action ID and one of the connectionNames shown; call get_action_guide first when the input is unclear.');
    for (const [service, provider] of native) {
      lines.push(directoryLine(`- ${service} (${[...provider.connections].join(', ')}): `, [...provider.actions].sort()));
    }
  }
  return lines.join('\n');
}

async function fetchReadToolDirectory(
  config: McpServiceConfig,
  service?: string,
  connectionName?: string,
  signal?: AbortSignal,
): Promise<ReadToolEntry[]> {
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
  const customConnections: string[] = [];
  const nativeServices = new Map<string, string[]>();
  for (const connection of allowedConnections) {
    if (connection.service === 'custom_mcp') {
      customConnections.push(connection.connectionName);
    } else {
      const names = nativeServices.get(connection.service) ?? [];
      names.push(connection.connectionName);
      nativeServices.set(connection.service, names);
    }
  }

  // Connections are independent: catalog them concurrently. Pages of one
  // native service stay sequential because each needs the previous cursor.
  const customEntries = customConnections.map(async (customConnection): Promise<ReadToolEntry[]> => {
    const result = await callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.list_tools',
      connectionName: customConnection,
      input: {},
    }, signal);
    const descriptors = payloadData(result).tools;
    if (!Array.isArray(descriptors)) throw new Error(`OpenConnector custom MCP ${customConnection} did not return a tool list`);
    return descriptors
      .filter((descriptor: any) => descriptor?.operationType === 'read' && descriptor.confirmationRequired !== true && typeof descriptor.name === 'string')
      .map((descriptor: any) => ({
        service: 'custom_mcp',
        connectionName: customConnection,
        actionId: 'custom_mcp.call_tool',
        toolName: descriptor.name,
        description: typeof descriptor.description === 'string' ? descriptor.description : '',
        inputSummary: descriptor.inputSchema,
      }));
  });
  const nativeEntries = [...nativeServices].map(async ([nativeService, connectionNames]): Promise<ReadToolEntry[]> => {
    const entries: ReadToolEntry[] = [];
    let cursor: string | undefined;
    const seenCursors = new Set<string>();
    do {
      const result = await callOpenConnectorTool(config, 'search_actions', {
        service: nativeService,
        limit: ACTION_SEARCH_PAGE_SIZE,
        ...(cursor ? { cursor } : {}),
      }, signal);
      const payload = parseOpenConnectorResult(result);
      if (!Array.isArray(payload.data)) throw new Error(`OpenConnector ${nativeService} did not return an action list`);
      for (const action of payload.data) {
        if (action?.operationType !== 'read' || action?.allowed === false || typeof action.id !== 'string') continue;
        for (const nativeConnection of connectionNames) {
          entries.push({
            service: nativeService,
            connectionName: nativeConnection,
            actionId: action.id,
            description: shortDescription(action.description),
          });
        }
      }
      const nextCursor = payload.page?.nextCursor;
      if (nextCursor != null && (typeof nextCursor !== 'string' || seenCursors.has(nextCursor))) {
        throw new Error(`OpenConnector ${nativeService} returned an invalid action cursor`);
      }
      if (nextCursor) seenCursors.add(nextCursor);
      cursor = nextCursor || undefined;
    } while (cursor);
    return entries;
  });
  const entries = (await Promise.all([...customEntries, ...nativeEntries])).flat();
  return entries.sort((a, b) =>
    `${a.service}/${a.connectionName}/${a.toolName || a.actionId}`.localeCompare(`${b.service}/${b.connectionName}/${b.toolName || b.actionId}`),
  );
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
        query: { type: 'string', description: 'Optional space-separated capability terms. Matches any term in action/tool name or description, e.g. "search wiki message document". Use focused discovery rather than paging through unrelated actions.' },
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
        const query = input.query === undefined ? '' : requiredString(input.query, 'query');
        const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
        const offset = input.offset === undefined ? 0 : input.offset;
        if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('OpenConnector directory offset must be a non-negative integer');
        const allEntries = await loadReadToolDirectory(config, service, connectionName, signal);
        const matching = allEntries.filter(entry =>
          (!service || entry.service === service) && (!connectionName || entry.connectionName === connectionName) &&
          (!terms.length || terms.some(term => `${entry.actionId} ${entry.toolName ?? ''} ${entry.description}`.toLowerCase().includes(term))),
        );
        const page = {
          total: matching.length,
          offset,
          nextOffset: offset + READ_TOOL_PAGE_SIZE < matching.length ? offset + READ_TOOL_PAGE_SIZE : null,
          tools: matching.slice(offset, offset + READ_TOOL_PAGE_SIZE).map(entry => ({
            service: entry.service,
            connectionName: entry.connectionName,
            actionId: entry.actionId,
            ...(entry.toolName ? { toolName: entry.toolName } : {}),
            description: shortDescription(entry.description),
          })),
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
