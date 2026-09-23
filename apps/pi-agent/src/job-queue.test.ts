import { expect, it, vi } from 'vitest';
import { claimNextJob, ticketIdFromDatabase } from './job-queue.js';

it('normalizes PostgreSQL bigint ticket ids before returning a claimed job', async () => {
  const client = {
    query: vi.fn(async (sql: string) => ({ rows: sql.includes('SELECT job_id')
      ? [{ job_id: 'job-1', ticket_id: '3726', prev_status: 'queued', attempts: 0 }]
      : [] })),
    release: vi.fn(),
  };
  const job = await claimNextJob({ connect: async () => client } as any, 'worker-1');
  expect(job?.ticket_id).toBe(3726);
  expect(typeof job?.ticket_id).toBe('number');
  expect(client.query).toHaveBeenLastCalledWith('COMMIT');
});

it.each(['9007199254740992', '0', '-1', '1.5', '1e3', null, undefined])(
  'rejects unsafe or malformed database ticket id %s', value => {
    expect(() => ticketIdFromDatabase(value)).toThrow('Invalid ticket_id');
  },
);

it('rolls back when a claimed job has an unsafe ticket id', async () => {
  const client = {
    query: vi.fn(async (sql: string) => ({ rows: sql.includes('SELECT job_id')
      ? [{ job_id: 'job-1', ticket_id: '9007199254740992', prev_status: 'queued' }]
      : [] })),
    release: vi.fn(),
  };
  await expect(claimNextJob({ connect: async () => client } as any, 'worker-1'))
    .rejects.toThrow('Invalid ticket_id');
  expect(client.query).toHaveBeenLastCalledWith('ROLLBACK');
  expect(client.release).toHaveBeenCalledOnce();
});
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
