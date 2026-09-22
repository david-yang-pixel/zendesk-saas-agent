export interface McpServiceConfig {
  name: string;
  endpoint: string;
  apiKey: string;
  excludedTools?: string[];
  protocolVersion?: '2026-07-28';
  executeTool?: (
    toolName: string,
    params: Record<string, any>,
    signal?: AbortSignal,
  ) => Promise<any>;
}

export interface CustomTool {
  name: string;
  label: string;
  description: string;
  promptSnippet: string;
  parameters: Record<string, any>;
  execute: (toolCallId: string, params: any, signal?: AbortSignal) => Promise<any>;
}

function protocolMeta(version?: string): Record<string, any> {
  return version ? { _meta: {
    'io.modelcontextprotocol/protocolVersion': version,
    'io.modelcontextprotocol/clientCapabilities': {},
  } } : {};
}

function protocolHeaders(version: string | undefined, method: string, name?: string): Record<string, string> {
  return version ? {
    'MCP-Protocol-Version': version,
    'MCP-Method': method,
    ...(name ? { 'MCP-Name': name } : {}),
  } : {};
}

async function parseMcpHttpResponse(res: Response): Promise<any> {
  const body = await res.text();
  const trimmed = body.trim();
  if (!trimmed) {
    throw new Error('MCP returned an empty response');
  }

  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    return JSON.parse(trimmed);
  }

  const messages: any[] = [];
  for (const frame of body.split(/\r?\n\r?\n/)) {
    const data = frame
      .split(/\r?\n/)
      .filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).trimStart())
      .join('\n')
      .trim();
    if (!data || data === '[DONE]') continue;
    try {
      messages.push(JSON.parse(data));
    } catch {
      continue;
    }
  }

  const response = [...messages]
    .reverse()
    .find((message: any) => message?.result !== undefined || message?.error !== undefined);
  if (response === undefined) {
    throw new Error('MCP returned an unreadable event stream');
  }
  return response;
}

export async function callMcpTool(
  endpoint: string,
  apiKey: string,
  toolName: string,
  toolArgs: Record<string, any>,
  signal?: AbortSignal,
  protocolVersion?: string,
): Promise<any> {
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      Authorization: `Bearer ${apiKey}`,
      ...protocolHeaders(protocolVersion, 'tools/call', toolName),
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: Date.now(),
      method: 'tools/call',
      params: {
        ...protocolMeta(protocolVersion),
        name: toolName,
        arguments: toolArgs || {},
      },
    }),
    signal,
  });

  if (!res.ok) {
    const text = await res.text();
    const error = new Error(`MCP tool ${toolName} call failed (${res.status}): ${text}`) as Error & { retryable?: boolean };
    try {
      if (JSON.parse(text).retryable === false) error.retryable = false;
    } catch { /* Non-JSON upstream errors still retain their HTTP status. */ }
    throw error;
  }

  const data = await parseMcpHttpResponse(res);
  if (data?.error) {
    throw new Error(`MCP tool ${toolName} error: ${data.error.message || JSON.stringify(data.error)}`);
  }

  return data?.result;
}

export async function discoverMcpTools(service: McpServiceConfig): Promise<CustomTool[]> {
  try {
    const res = await fetch(service.endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        Authorization: `Bearer ${service.apiKey}`,
        ...protocolHeaders(service.protocolVersion, 'tools/list'),
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'tools/list',
        params: protocolMeta(service.protocolVersion),
      }),
    });

    if (!res.ok) {
      console.warn(`[MCP] Failed to list tools from ${service.name} (${service.endpoint}): ${res.status}`);
      return [];
    }

    const data = await parseMcpHttpResponse(res);
    const rawTools = data?.result?.tools;
    if (!Array.isArray(rawTools)) {
      return [];
    }

    const excluded = new Set(service.excludedTools || []);
    const tools: CustomTool[] = [];

    for (const tool of rawTools) {
      if (!tool.name || excluded.has(tool.name)) {
        continue;
      }

      tools.push({
        name: tool.name,
        label: tool.name,
        description: tool.description || `Tool ${tool.name}`,
        promptSnippet: (tool.description || tool.name).slice(0, 100),
        parameters: tool.inputSchema || { type: 'object', properties: {} },
        execute: async (_toolCallId: string, params: any, signal?: AbortSignal) => {
          try {
            const mcpResult = service.executeTool
              ? await service.executeTool(tool.name, params || {}, signal)
              : await callMcpTool(service.endpoint, service.apiKey, tool.name, params || {}, signal, service.protocolVersion);
            const serialized = typeof mcpResult === 'string' ? mcpResult : JSON.stringify(mcpResult, null, 2);
            return {
              content: [{ type: 'text', text: serialized }],
              details: mcpResult,
            };
          } catch (err: any) {
            return {
              content: [{ type: 'text', text: `Tool error: ${err.message || String(err)}` }],
              details: { error: err.message || String(err) },
            };
          }
        },
      });
    }

    console.log(`[MCP] Discovered ${tools.length} tools from ${service.name}: ${tools.map(t => t.name).join(', ')}`);
    return tools;
  } catch (err: any) {
    console.error(`[MCP] Error connecting to ${service.name} (${service.endpoint}):`, err.message);
    return [];
  }
}
