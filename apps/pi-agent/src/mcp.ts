export interface McpServiceConfig {
  name: string;
  endpoint: string;
  apiKey: string;
  excludedTools?: string[];
}

export interface CustomTool {
  name: string;
  label: string;
  description: string;
  promptSnippet: string;
  parameters: Record<string, any>;
  execute: (toolCallId: string, params: any, signal?: AbortSignal) => Promise<any>;
}

export async function callMcpTool(
  endpoint: string,
  apiKey: string,
  toolName: string,
  toolArgs: Record<string, any>
): Promise<any> {
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: Date.now(),
      method: 'tools/call',
      params: {
        name: toolName,
        arguments: toolArgs || {},
      },
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`MCP tool ${toolName} call failed (${res.status}): ${text}`);
  }

  const data = (await res.json()) as any;
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
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'tools/list',
        params: {},
      }),
    });

    if (!res.ok) {
      console.warn(`[MCP] Failed to list tools from ${service.name} (${service.endpoint}): ${res.status}`);
      return [];
    }

    const data = (await res.json()) as any;
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
        execute: async (_toolCallId: string, params: any, _signal?: AbortSignal) => {
          try {
            const mcpResult = await callMcpTool(service.endpoint, service.apiKey, tool.name, params || {});
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
