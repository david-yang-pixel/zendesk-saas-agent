import { describe, expect, it, vi } from 'vitest';
import type { CustomTool } from './mcp.js';
import { withRunDiscoveryCache } from './openconnector.js';

function discovery(name = 'get_action_guide') {
  const execute = vi.fn<CustomTool['execute']>().mockResolvedValue({ details: { ok: true } });
  return { name, label: name, description: '', promptSnippet: '', parameters: {}, execute };
}

describe('discovery within one support run', () => {
  it('reuses an exact guide only in the same run, independent of key order', async () => {
    const tool = discovery();
    const run = withRunDiscoveryCache(tool);
    const args = { actionId: 'example.read', connectionName: 'a' };
    const first = await run.execute('1', args);
    first.details.ok = false;
    expect((await run.execute('2', { connectionName: 'a', actionId: 'example.read' })).details.ok).toBe(true);
    await run.execute('3', { ...args, connectionName: 'b' });
    await withRunDiscoveryCache(tool).execute('4', args);
    expect(tool.execute).toHaveBeenCalledTimes(3);
  });

  it('keeps pages distinct and retries errors', async () => {
    const tool = discovery('list_connections');
    tool.execute.mockResolvedValueOnce({ isError: true });
    const run = withRunDiscoveryCache(tool);
    await run.execute('1', {});
    await run.execute('2', {});
    await run.execute('3', { cursor: 'next' });
    await run.execute('4', { cursor: 'next' });
    expect(tool.execute).toHaveBeenCalledTimes(3);
    await expect(run.execute('5', {}, AbortSignal.abort())).rejects.toThrow();
  });

  it('executes every business read', async () => {
    const tool = discovery('execute_action');
    const run = withRunDiscoveryCache(tool);
    await run.execute('1', {});
    await run.execute('2', {});
    expect(tool.execute).toHaveBeenCalledTimes(2);
  });
});
