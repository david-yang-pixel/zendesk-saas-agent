import { afterEach, expect, it, vi } from 'vitest';
import { discoverMcpTools } from './mcp.js';
afterEach(() => vi.unstubAllGlobals());
it('marks a business-error response as failed at the Pi tool boundary', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ result: { tools: [{ name: 'execute_action' }] } }))));
  const [tool] = await discoverMcpTools({ name: 'test', endpoint: 'https://example.test', apiKey: 'test',
    executeTool: async () => ({ isError: false, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { message: 'bad cursor' } }) }] }),
  });
  expect((await tool.execute('test-call', {})).isError).toBe(true);
});
it('marks transport exceptions as failed without losing their message', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ result: { tools: [{ name: 'execute_action' }] } }))));
  const [tool] = await discoverMcpTools({ name: 'test', endpoint: 'https://example.test', apiKey: 'test',
    executeTool: async () => { throw new Error('HTTP 503'); },
  });
  expect(await tool.execute('test-call', {})).toMatchObject({ isError: true, details: { error: 'HTTP 503' } });
});
