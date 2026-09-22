import { afterEach, expect, it, vi } from 'vitest';
import { callOpenConnectorTool, createOpenConnectorServiceConfig, validateCustomMcpPagination } from './openconnector.js';
afterEach(() => vi.unstubAllGlobals());
it('accepts wrapped cursors only at the connector pagination level', () => {
  expect(() => validateCustomMcpPagination({ pagination: { mode: 'single', cursor: 'ocmcp1.opaque', pageSize: 25 } })).not.toThrow();
  expect(() => validateCustomMcpPagination({ arguments: { conversation_after: 'ocmcp1.opaque' } })).toThrow('input.pagination.cursor');
});
it('preserves native cursors for the deterministic worker pager', () => {
  expect(() => validateCustomMcpPagination({ arguments: { conversation_after: 'native-cursor' } })).not.toThrow();
  expect(() => validateCustomMcpPagination({ pagination: { cursor: 'native-cursor' } })).toThrow('opaque OpenConnector');
});
it('rejects conflicting cursors and validates dynamically configured cursor fields', () => {
  expect(() => validateCustomMcpPagination({ arguments: { cursor: 'ocmcp1.opaque' } }, { requestCursorField: 'cursor' })).toThrow('input.arguments.cursor');
  expect(() => validateCustomMcpPagination({ arguments: { conversation_after: 'native' }, pagination: { cursor: 'ocmcp1.opaque' } })).toThrow('never both');
});
it('blocks an invalid cursor before the upstream tool call', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ result: { structuredContent: {
    ok: true, data: { tools: [{ name: 'get_ticket_conversation_log', operationType: 'read', pagination: { requestCursorField: 'conversation_after' } }] },
  } } })));
  vi.stubGlobal('fetch', fetcher);
  await expect(callOpenConnectorTool(createOpenConnectorServiceConfig('https://example.test/mcp', 'test'), 'execute_action', {
    actionId: 'custom_mcp.call_tool', connectionName: 'zendesk', input: { toolName: 'get_ticket_conversation_log', arguments: { conversation_after: 'ocmcp1.opaque' } },
  })).rejects.toThrow('input.pagination.cursor');
  expect(fetcher).toHaveBeenCalledTimes(1); // read-only policy preflight only
});

it('forwards a valid connector continuation without rewriting or decoding its cursor', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ result: { structuredContent: { ok: true, data: { tools: [{ name: 'get_ticket_conversation_log', operationType: 'read' }] } } } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ result: { structuredContent: { success: true, data: { events: [{ id: 'next-event' }] } } } })));
  vi.stubGlobal('fetch', fetcher);
  const input = { toolName: 'get_ticket_conversation_log', arguments: { ticket_id: 3726 }, pagination: { mode: 'single', cursor: 'ocmcp1.opaque', pageSize: 25 } };
  await callOpenConnectorTool(createOpenConnectorServiceConfig('https://example.test/mcp', 'test'), 'execute_action', {
    actionId: 'custom_mcp.call_tool', connectionName: 'zendesk', input,
  });
  expect(JSON.parse(fetcher.mock.calls[1][1].body).params.arguments.input).toEqual(input);
});
