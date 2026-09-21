import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  callOpenConnectorTool,
  createOpenConnectorServiceConfig,
  discoverOpenConnectorTools,
  inferOpenConnectorService,
  validateOpenConnectorControlCall,
} from './openconnector.js';

function mcpResponse(payload: unknown): Response {
  return new Response(JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    result: {
      content: [{ type: 'text', text: JSON.stringify(payload) }],
    },
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function sseMcpResponse(result: unknown): Response {
  return new Response(`event: message\ndata: ${JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    result,
  })}\n\n`, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('OpenConnector hard read-only boundary', () => {
  it('discovers controls from a streamable HTTP event response', async () => {
    const fetchMock = vi.fn(async () => sseMcpResponse({
      tools: [
        { name: 'search_actions', description: 'Search actions', inputSchema: { type: 'object' } },
        { name: 'list_apps', description: 'List apps', inputSchema: { type: 'object' } },
      ],
    }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    const tools = await discoverOpenConnectorTools(config);

    expect(tools.map(tool => tool.name)).toEqual(['search_actions']);
  });

  it('reads tool results from a streamable HTTP event response', async () => {
    const fetchMock = vi.fn(async () => sseMcpResponse({
      content: [{ type: 'text', text: JSON.stringify({ ok: true, data: { connections: [] } }) }],
    }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    await expect(callOpenConnectorTool(config, 'list_connections', {
      service: 'custom_mcp',
    })).resolves.toEqual({
      content: [{ type: 'text', text: JSON.stringify({ ok: true, data: { connections: [] } }) }],
    });
  });

  it('requires scoped discovery and blocks Gmail', () => {
    expect(() => validateOpenConnectorControlCall('list_connections', {})).toThrow(/service filter/);
    expect(() => validateOpenConnectorControlCall('search_actions', { service: 'gmail' })).toThrow(/disabled/);
    expect(() => validateOpenConnectorControlCall('list_apps', {})).toThrow(/not exposed/);
  });

  it('blocks a dynamically classified custom MCP write before invoking it', async () => {
    const fetchMock = vi.fn(async () => mcpResponse({
      ok: true,
      data: {
        tools: [{ name: 'update_ticket_status', operationType: 'write', confirmationRequired: true }],
      },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    await expect(callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.call_tool',
      connectionName: 'zendesk',
      input: { toolName: 'update_ticket_status', arguments: { ticket_id: 1 } },
    })).rejects.toThrow(/only read tools are allowed/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects confirmOperation without contacting OpenConnector', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    await expect(callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.call_tool',
      connectionName: 'zendesk',
      input: {
        toolName: 'update_ticket_status',
        arguments: { ticket_id: 1 },
        confirmOperation: 'write',
      },
    })).rejects.toThrow(/confirmOperation is forbidden/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('allows a dynamically classified custom MCP read and invokes it without confirmation', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mcpResponse({
        ok: true,
        data: {
          tools: [{ name: 'get_ticket', operationType: 'read', confirmationRequired: false }],
        },
      }))
      .mockResolvedValueOnce(mcpResponse({ ok: true, data: { ticket: { id: 1 } } }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    await callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.call_tool',
      connectionName: 'zendesk',
      input: { toolName: 'get_ticket', arguments: { ticket_id: 1 } },
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondRequest = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(secondRequest.params.arguments.input).toEqual({
      toolName: 'get_ticket',
      arguments: { ticket_id: 1 },
    });
    expect(secondRequest.params.arguments.input).not.toHaveProperty('confirmOperation');
  });

  it('blocks native provider writes after reading the live action guide', async () => {
    const fetchMock = vi.fn(async () => mcpResponse({
      ok: true,
      data: { capability: { operationType: 'write' } },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    await expect(callOpenConnectorTool(config, 'execute_action', {
      actionId: 'linear.create_issue',
      connectionName: 'default',
      input: { title: 'must not be created' },
    })).rejects.toThrow(/only read actions are allowed/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('allows a native provider read after checking the live action guide', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mcpResponse({
        ok: true,
        data: { capability: { operationType: 'read' } },
      }))
      .mockResolvedValueOnce(mcpResponse({ ok: true, data: { issues: [] } }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    await callOpenConnectorTool(config, 'execute_action', {
      actionId: 'linear.search_issues',
      connectionName: 'default',
      input: { query: 'runtime error' },
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('attributes OpenConnector calls to their underlying evidence service', () => {
    expect(inferOpenConnectorService('execute_action', {
      actionId: 'custom_mcp.call_tool', connectionName: 'github-mcp',
    })).toBe('github');
    expect(inferOpenConnectorService('execute_action', {
      actionId: 'feishu.search_messages', connectionName: 'default',
    })).toBe('feishu');
    expect(inferOpenConnectorService('list_connections', { service: 'custom_mcp' })).toBe('custom_mcp');
  });
});
