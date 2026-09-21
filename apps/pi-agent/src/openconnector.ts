import {
  callMcpTool,
  discoverMcpTools,
  type CustomTool,
  type McpServiceConfig,
} from './mcp.js';

export const OPENCONNECTOR_AGENT_TOOLS = [
  'list_connections',
  'search_actions',
  'get_action_guide',
  'execute_action',
] as const;

const OPENCONNECTOR_TOOL_SET = new Set<string>(OPENCONNECTOR_AGENT_TOOLS);
const BLOCKED_SERVICES = new Set(['gmail']);

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
  return callMcpTool(
    config.endpoint,
    config.apiKey,
    'execute_action',
    { actionId, connectionName, input },
    signal,
  );
}

async function assertNativeActionIsReadOnly(
  config: McpServiceConfig,
  actionId: string,
  connectionName: string,
  signal?: AbortSignal,
): Promise<void> {
  const guide = await callMcpTool(
    config.endpoint,
    config.apiKey,
    'get_action_guide',
    { actionId, connectionName },
    signal,
  );
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
): Promise<void> {
  const listResult = await callRawOpenConnectorAction(
    config,
    'custom_mcp.list_tools',
    connectionName,
    {},
    signal,
  );
  const tools = payloadData(listResult).tools;
  if (!Array.isArray(tools)) {
    throw new Error(`OpenConnector custom MCP ${connectionName} did not return a tool list`);
  }
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
      await assertCustomMcpToolIsReadOnly(config, connectionName, remoteToolName, signal);
    } else if (actionId !== 'custom_mcp.list_tools') {
      await assertNativeActionIsReadOnly(config, actionId, connectionName, signal);
    }
  }

  return callMcpTool(config.endpoint, config.apiKey, toolName, params, signal);
}

export function createOpenConnectorServiceConfig(endpoint: string, apiKey: string): McpServiceConfig {
  const config: McpServiceConfig = {
    name: 'openconnector',
    endpoint,
    apiKey,
    excludedTools: ['list_apps'],
  };
  config.executeTool = (toolName, params, signal) => callOpenConnectorTool(config, toolName, params, signal);
  return config;
}

export async function discoverOpenConnectorTools(config: McpServiceConfig): Promise<CustomTool[]> {
  const tools = await discoverMcpTools(config);
  return tools.filter((tool) => OPENCONNECTOR_TOOL_SET.has(tool.name));
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
