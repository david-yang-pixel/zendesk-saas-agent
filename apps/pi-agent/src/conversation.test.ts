import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadConversation } from './conversation.js';
import { createOpenConnectorServiceConfig, parseOpenConnectorResult, callOpenConnectorTool } from './openconnector.js';

function reply(payload: unknown, structured = false): Response {
  return new Response(JSON.stringify({ jsonrpc: '2.0', id: 1, result: structured
    ? { structuredContent: payload }
    : { content: [{ type: 'text', text: JSON.stringify(payload) }] } }));
}
const config = () => createOpenConnectorServiceConfig('https://connector.test/mcp', 'test');
const list = () => reply({ ok: true, data: { tools: [{ name: 'get_ticket_conversation_log', operationType: 'read' }] } });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('conversation read recovery', () => {
  it('uses the complete per-request MCP envelope instead of the legacy SSE route', async () => {
    const fetcher = vi.fn(async () => reply({ ok: true, data: {} }));
    vi.stubGlobal('fetch', fetcher);
    await callOpenConnectorTool(config(), 'list_connections', { service: 'zendesk' });
    const request = fetcher.mock.calls[0][1];
    expect(request.headers).toMatchObject({ 'MCP-Protocol-Version': '2026-07-28', 'MCP-Method': 'tools/call', 'MCP-Name': 'list_connections' });
    expect(JSON.parse(request.body).params._meta).toEqual({
      'io.modelcontextprotocol/protocolVersion': '2026-07-28',
      'io.modelcontextprotocol/clientCapabilities': {},
    });
  });
  it('recovers empty preflight and empty read responses without losing pagination', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(''))
      .mockResolvedValueOnce(list())
      .mockResolvedValueOnce(new Response(''))
      .mockResolvedValueOnce(reply({ success: true, data: { events: [{ id: 1 }], meta: { has_more: true, after_cursor: 'next' } } }, true))
      .mockResolvedValueOnce(reply({ success: true, data: { events: [{ id: 2 }], meta: { has_more: false } } }));
    vi.stubGlobal('fetch', fetcher);
    const pending = loadConversation(3726, config());
    const assertion = expect(pending).resolves.toEqual([{ id: 1 }, { id: 2 }]);
    await vi.runAllTimersAsync();
    await assertion;
    expect(JSON.parse(fetcher.mock.calls[4][1].body).params.arguments.input.arguments.conversation_after).toBe('next');
  });

  it.each([undefined, 'same'])('refuses missing/repeated next cursor %s', async cursor => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(list()).mockImplementation(async () =>
      reply({ success: true, data: { events: [], meta: { has_more: true, after_cursor: cursor } } })));
    await expect(loadConversation(3726, config())).rejects.toThrow(/missing or repeated cursor/);
  });

  it('surfaces upstream tool failure instead of calling it invalid conversation', () => {
    expect(() => parseOpenConnectorResult({ isError: true, structuredContent: { ok: false, error: { message: 'upstream unavailable' } } })).toThrow('upstream unavailable');
  });

  it('stops after three empty responses', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => new Response(''));
    vi.stubGlobal('fetch', fetcher);
    const assertion = expect(callOpenConnectorTool(config(), 'list_connections', { service: 'zendesk' })).rejects.toThrow('empty response');
    await vi.runAllTimersAsync();
    await assertion;
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('does not retry unauthorized requests', async () => {
    const fetcher = vi.fn(async () => new Response('unauthorized', { status: 401 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(callOpenConnectorTool(config(), 'list_connections', { service: 'zendesk' })).rejects.toThrow('401');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not retry a Cloudflare resource limit explicitly marked non-retryable', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ error_code: 1102, retryable: false }), { status: 503 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(callOpenConnectorTool(config(), 'list_connections', { service: 'zendesk' })).rejects.toThrow('1102');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not call upstream after cancellation', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await expect(callOpenConnectorTool(config(), 'list_connections', { service: 'zendesk' }, AbortSignal.abort())).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
