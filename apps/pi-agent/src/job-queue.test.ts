import { expect, it, vi } from 'vitest';
import { claimNextJob } from './job-queue.js';
it('commits expired-job cleanup even when no job can be claimed', async () => {
  const client = { query: vi.fn(async () => ({ rows: [] })), release: vi.fn() };
  const pool = { connect: vi.fn(async () => client) };
  expect(await claimNextJob(pool as any, 'test')).toBeNull();
  const queries = client.query.mock.calls.map(call => call[0]);
  expect(queries[1]).toContain('lease_owner = NULL');
  expect(queries.at(-1)).toBe('COMMIT');
  expect(queries).not.toContain('ROLLBACK');
  expect(client.release).toHaveBeenCalledOnce();
});
it('rolls back and releases the client when claiming fails', async () => {
  const client = { query: vi.fn().mockResolvedValueOnce({ rows: [] }).mockRejectedValueOnce(new Error('DB failure')).mockResolvedValue({ rows: [] }), release: vi.fn() };
  await expect(claimNextJob({ connect: async () => client } as any, 'test')).rejects.toThrow('DB failure');
  expect(client.query).toHaveBeenLastCalledWith('ROLLBACK');
  expect(client.release).toHaveBeenCalledOnce();
});
