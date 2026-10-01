import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  assertDbGatewaySqlHasClosedStrings,
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
  it.each(['', '  ', null])('omits an empty optional service filter (%s) before discovery', async service => {
    const fetchMock = vi.fn(async () => mcpResponse({ ok: true, data: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await callOpenConnectorTool(createOpenConnectorServiceConfig('https://example.test/mcp', 'test'), 'list_connections', { service });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).params.arguments).toEqual({});
  });
  it('rejects an unterminated DB string without exposing SQL in the error', () => {
    expect(() => assertDbGatewaySqlHasClosedStrings("SELECT 1 WHERE tenant_id IN ('first','second)"))
      .toThrow(/unterminated quoted string/);
    expect(() => assertDbGatewaySqlHasClosedStrings("SELECT 'O''Brien', E'escaped\\'quote', $$literal'$$ -- ' comment"))
      .not.toThrow();
  });

  it('stops malformed DB SQL after read classification but before remote execution', async () => {
    const fetchMock = vi.fn(async () => mcpResponse({ ok: true, data: {
      tools: [{ name: 'db_gateway_query', operationType: 'read', confirmationRequired: false }],
    } }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    await expect(callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.call_tool', connectionName: 'dify-db',
      input: { toolName: 'db_gateway_query', arguments: { database: 'prod', sql: "SELECT 1 WHERE tenant_id IN ('first','second)" } },
    })).rejects.toThrow(/no database request was sent/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('accepts Pi-qualified Custom MCP read names only after canonical read-policy verification', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mcpResponse({ ok: true, data: {
        tools: [{ name: 'get_ticket', operationType: 'read', confirmationRequired: false }],
      } }))
      .mockResolvedValueOnce(mcpResponse({ ok: true, data: { ticket: { id: 3726 } } }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    await expect(callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.call_tool', connectionName: 'zendesk',
      input: { toolName: 'custom_mcp.remote.get_ticket', arguments: { ticket_id: 3726 } },
    })).resolves.toBeDefined();
    const executed = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(executed.params.arguments.input.toolName).toBe('get_ticket');
  });

  it('does not use qualified names to bypass a Custom MCP write classification', async () => {
    const fetchMock = vi.fn(async () => mcpResponse({ ok: true, data: {
      tools: [{ name: 'update_ticket_status', operationType: 'write', confirmationRequired: true }],
    } }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    await expect(callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.call_tool', connectionName: 'zendesk',
      input: { toolName: 'custom_mcp.remote.update_ticket_status', arguments: { ticket_id: 3726 } },
    })).rejects.toThrow(/only read tools are allowed/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('discovers controls from a streamable HTTP event response', async () => {
    const fetchMock = vi.fn(async () => sseMcpResponse({
      tools: [
        { name: 'list_connections', description: 'List connections', inputSchema: { type: 'object' } },
        { name: 'search_actions', description: 'Search actions', inputSchema: { type: 'object' } },
        { name: 'get_action_guide', description: 'Get guide', inputSchema: { type: 'object' } },
        { name: 'execute_action', description: 'Execute', inputSchema: { type: 'object' } },
        { name: 'list_apps', description: 'List apps', inputSchema: { type: 'object' } },
      ],
    }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');

    const tools = await discoverOpenConnectorTools(config);

    expect(tools.map(tool => tool.name)).toEqual([
      'list_connections', 'search_actions', 'get_action_guide', 'execute_action', 'list_read_tools',
    ]);
    expect(tools.find(tool => tool.name === 'get_action_guide')?.parameters.required).toEqual(['actionId', 'connectionName']);
    expect(tools.find(tool => tool.name === 'execute_action')?.parameters.required).toEqual(['actionId', 'connectionName']);
    expect(tools.find(tool => tool.name === 'search_actions')?.parameters.required).toEqual(['service']);
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

  it('allows discovery across every connected service but blocks Gmail execution', () => {
    expect(() => validateOpenConnectorControlCall('list_connections', {})).not.toThrow();
    expect(() => validateOpenConnectorControlCall('search_actions', {})).toThrow(/service filter/);
    expect(() => validateOpenConnectorControlCall('search_actions', { service: 'gmail' })).toThrow(/disabled/);
    expect(() => validateOpenConnectorControlCall('execute_action', {
      actionId: 'custom_mcp.list_tools', connectionName: 'gmail-mcp', input: {},
    })).toThrow(/disabled/);
    expect(() => validateOpenConnectorControlCall('list_apps', {})).toThrow(/not exposed/);
  });

  it('hides Gmail from unscoped connected-service discovery', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mcpResponse({ ok: true, data: [
      { service: 'gmail', connectionName: 'default' },
      { service: 'custom_mcp', connectionName: 'gmail-mcp' },
      { service: 'feishu', connectionName: 'default' },
    ] })));
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    const result = await callOpenConnectorTool(config, 'list_connections', {});
    expect(result.structuredContent.data).toEqual([{ service: 'feishu', connectionName: 'default' }]);
  });

  it('keeps every native read action discoverable without returning repeated provider profiles', async () => {
    const largeProfile = { grantedScopes: Array.from({ length: 1000 }, (_, index) => `scope-${index}`) };
    vi.stubGlobal('fetch', vi.fn(async () => mcpResponse({ ok: true, data: [
      { id: 'feishu.search_documents', operationType: 'read', description: 'Search documents', capability: { connection: { profile: largeProfile } } },
      { id: 'feishu.search_messages', operationType: 'read', description: 'Search messages', capability: { connection: { profile: largeProfile } } },
      { id: 'feishu.update_document', operationType: 'write', description: 'Update document', capability: { connection: { profile: largeProfile } } },
    ], page: { nextCursor: null } })));
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    const result = await callOpenConnectorTool(config, 'search_actions', { service: 'feishu' });
    expect(result.structuredContent.data.map((action: any) => action.id)).toEqual([
      'feishu.search_documents', 'feishu.search_messages', 'feishu.update_document',
    ]);
    expect(JSON.stringify(result).length).toBeLessThan(2000);
    expect(JSON.stringify(result)).not.toContain('grantedScopes');
  });

  it('keeps a selected guide and read-only Custom MCP schema while omitting connection profiles and write tools', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mcpResponse({ ok: true, data: {
        capability: { operationType: 'read', policy: { allowed: true }, connection: { profile: { grantedScopes: Array(1000).fill('scope') } } },
        markdown: '## Input Parameters\nquery: string\n\n## Current Connection\nGranted scopes: many\n\n## Notes For Agents\nCall execute_action',
      } }))
      .mockResolvedValueOnce(mcpResponse({ ok: true, data: { tools: [
        { name: 'search_docs', operationType: 'read', confirmationRequired: false, inputSchema: { required: ['query'] }, connection: { profile: 'large' } },
        { name: 'write_doc', operationType: 'write', confirmationRequired: true },
      ] } }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    const guide = await callOpenConnectorTool(config, 'get_action_guide', { actionId: 'feishu.search_documents', connectionName: 'default' });
    expect(JSON.stringify(guide)).toContain('query: string');
    expect(JSON.stringify(guide)).not.toContain('Granted scopes: many');
    expect(JSON.stringify(guide)).not.toContain('grantedScopes');
    const tools = await callOpenConnectorTool(config, 'execute_action', {
      actionId: 'custom_mcp.list_tools', connectionName: 'dify-docs', input: {},
    });
    expect(tools.structuredContent.data.tools).toEqual([{
      name: 'search_docs', description: '', operationType: 'read', confirmationRequired: false,
      inputSchema: { required: ['query'] },
    }]);
    expect(JSON.stringify(tools)).not.toContain('write_doc');
    expect(JSON.stringify(tools)).not.toContain('profile');
  });

  it('catalogs all read tools from connected native and Custom MCP services without listing Gmail', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      if (request.method === 'tools/list') return sseMcpResponse({ tools: [
        { name: 'list_connections' }, { name: 'search_actions' },
        { name: 'get_action_guide' }, { name: 'execute_action' },
      ] });
      const { name, arguments: args } = request.params;
      if (name === 'list_connections') return mcpResponse({ ok: true, data: [
        { service: 'feishu', connectionName: 'default' },
        { service: 'custom_mcp', connectionName: 'dify-docs' },
        { service: 'custom_mcp', connectionName: 'github-mcp' },
        { service: 'gmail', connectionName: 'default' },
      ] });
      if (name === 'search_actions') return mcpResponse({ ok: true, data: [
        { id: 'feishu.search_docs', operationType: 'read', description: 'Search Feishu docs' },
        { id: 'feishu.update_doc', operationType: 'write', description: 'Modify a doc' },
      ], page: { nextCursor: null } });
      if (name === 'execute_action' && args.actionId === 'custom_mcp.list_tools') {
        return mcpResponse({ ok: true, data: { tools: args.connectionName === 'dify-docs'
          ? [
            { name: 'official_docs_search', operationType: 'read', confirmationRequired: false },
            { name: 'edit_article', operationType: 'write', confirmationRequired: true },
          ]
          : [
            { name: 'github_code_search', operationType: 'read', confirmationRequired: false },
            { name: 'github_create_issue', operationType: 'write', confirmationRequired: true },
          ] } });
      }
      throw new Error(`Unexpected request: ${name}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    const directory = (await discoverOpenConnectorTools(config)).find(tool => tool.name === 'list_read_tools');
    const result = await directory!.execute('call-1', {});
    expect(result.isError).not.toBe(true);
    expect(result.details.tools.map((tool: any) => `${tool.connectionName}/${tool.toolName || tool.actionId}`)).toEqual([
      'dify-docs/official_docs_search', 'github-mcp/github_code_search', 'default/feishu.search_docs',
    ]);
    expect(fetchMock.mock.calls.some(([, init]) => JSON.stringify(init?.body).includes('gmail.'))).toBe(false);
  });

  it('pages a service-scoped read directory and rejects a Gmail scope before network use', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      if (request.method === 'tools/list') return sseMcpResponse({ tools: [
        { name: 'list_connections' }, { name: 'search_actions' },
        { name: 'get_action_guide' }, { name: 'execute_action' },
      ] });
      if (request.params.name === 'list_connections') return mcpResponse({ ok: true, data: [
        { service: 'custom_mcp', connectionName: 'dify-docs' },
      ] });
      if (request.params.name === 'execute_action') return mcpResponse({ ok: true, data: {
        tools: Array.from({ length: 51 }, (_, index) => ({
          name: `read_${index}`, operationType: 'read', confirmationRequired: false,
        })),
      } });
      throw new Error('Unexpected request');
    });
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    const directory = (await discoverOpenConnectorTools(config)).find(tool => tool.name === 'list_read_tools')!;
    const initialCalls = fetchMock.mock.calls.length;
    const denied = await directory.execute('call-1', { service: 'gmail' });
    expect(denied.isError).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(initialCalls);
    const first = await directory.execute('call-2', { service: 'custom_mcp', connectionName: 'dify-docs' });
    const second = await directory.execute('call-3', { service: 'custom_mcp', connectionName: 'dify-docs', offset: 50 });
    expect(first.details).toMatchObject({ total: 51, nextOffset: 50 });
    expect(second.details).toMatchObject({ total: 51, nextOffset: null });
    expect(second.details.tools).toHaveLength(1);
    const filtered = await directory.execute('call-filter', { service: 'custom_mcp', connectionName: 'dify-docs', query: 'read_9' });
    expect(filtered.details).toMatchObject({ total: 1, nextOffset: null });
    expect(filtered.details.tools[0].toolName).toBe('read_9');
    const noMatch = await directory.execute('call-empty', { service: 'custom_mcp', connectionName: 'dify-docs', query: 'not-a-tool' });
    expect(noMatch.details).toMatchObject({ total: 0, tools: [] });
    expect(fetchMock).toHaveBeenCalledTimes(initialCalls + 2);
  });

  it('continues native action pages so the directory does not silently omit later reads', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      if (request.method === 'tools/list') return sseMcpResponse({ tools: [
        { name: 'list_connections' }, { name: 'search_actions' },
        { name: 'get_action_guide' }, { name: 'execute_action' },
      ] });
      if (request.params.name === 'list_connections') return mcpResponse({ ok: true, data: [
        { service: 'feishu', connectionName: 'default' },
      ] });
      if (request.params.name === 'search_actions') {
        const cursor = request.params.arguments.cursor;
        return mcpResponse(cursor
          ? { ok: true, data: [{ id: 'feishu.search_policy', operationType: 'read' }], page: { nextCursor: null } }
          : { ok: true, data: [
            { id: 'feishu.search_docs', operationType: 'read' },
            { id: 'feishu.edit_doc', operationType: 'write' },
            { id: 'feishu.hidden_read', operationType: 'read', capability: { policy: { allowed: false } } },
          ], page: { nextCursor: 'oas1.second-page' } });
      }
      throw new Error('Unexpected request');
    });
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    const directory = (await discoverOpenConnectorTools(config)).find(tool => tool.name === 'list_read_tools')!;
    const result = await directory.execute('call-1', { service: 'feishu' });
    expect(result.details.tools.map((tool: any) => tool.actionId)).toEqual([
      'feishu.search_docs', 'feishu.search_policy',
    ]);
    expect(fetchMock.mock.calls.filter(([, init]) => JSON.parse(String(init?.body)).params?.name === 'search_actions')).toHaveLength(2);
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

  it('reuses a recent custom MCP read classification instead of listing tools before every call', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mcpResponse({
        ok: true,
        data: {
          tools: [{ name: 'get_ticket', operationType: 'read', confirmationRequired: false }],
        },
      }))
      .mockImplementation(async () => mcpResponse({ ok: true, data: { ticket: { id: 1 } } }));
    vi.stubGlobal('fetch', fetchMock);
    const config = createOpenConnectorServiceConfig('https://connector.example/mcp', 'runtime-token');
    const params = {
      actionId: 'custom_mcp.call_tool',
      connectionName: 'zendesk',
      input: { toolName: 'get_ticket', arguments: { ticket_id: 1 } },
    };

    await callOpenConnectorTool(config, 'execute_action', params);
    await callOpenConnectorTool(config, 'execute_action', params);

    expect(fetchMock).toHaveBeenCalledTimes(3);
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
