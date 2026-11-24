import pg from 'pg';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { notifySnapshotWaits, oldestWaitingTicket } from './queue-alerts.js';

const dbUrl = process.env.TEST_DATABASE_URL;
describe.skipIf(!dbUrl)('queue operations alerts', () => {
  let db: pg.Pool;
  beforeAll(async () => {
    db = new pg.Pool({ connectionString: dbUrl?.replace(/^postgresql\+\w+:/, 'postgresql:'), max: 1 });
    // Use the migrated column types and defaults while isolating the monitor from other test workers.
    for (const table of ['ticket_states', 'events', 'case_jobs']) {
      await db.query(`CREATE TEMP TABLE ${table} AS SELECT * FROM public.${table} WITH NO DATA`);
    }
    await db.query(`INSERT INTO ticket_states(ticket_id,generation,latest_customer_seq,answered_customer_seq) VALUES (12,1,2,1),(13,1,1,0)`);
    await db.query(`INSERT INTO case_jobs(job_id,ticket_id,status,generation,is_operator_mention,dead_letter_reason,created_at,snapshot_wait_since)
      VALUES ('older',12,'queued',1,false,'waiting_for_conversation_snapshot',NOW()-interval '2 days',NOW()-interval '20 minutes'),
             ('recent',13,'queued',1,false,'waiting_for_conversation_snapshot',NOW()-interval '1 hour',NOW()-interval '2 minutes')`);
    await db.query(`INSERT INTO events(ticket_id,customer_seq,received_at,answered_by_staff) VALUES
      (12,1,NOW()-interval '1 day',TRUE), (12,2,NOW()-interval '22 minutes',FALSE), (13,1,NOW()-interval '3 minutes',FALSE)`);
  });
  afterAll(async () => { await db.end(); });

  it('measures the oldest outstanding customer message independently of job creation time', async () => {
    expect(await oldestWaitingTicket(db)).toEqual({ ticket_id: '12', minutes: 22 });
  });

  it('throttles reminders across scans and retries failed notifications sooner', async () => {
    const notify = vi.fn(async () => false);
    await notifySnapshotWaits(db, notify, 'test');
    await notifySnapshotWaits(db, notify, 'test');
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith('test', expect.objectContaining({ ticketId: 12 }), expect.any(Function));
    const window = await db.query("SELECT snapshot_alert_after < NOW()+interval '3 minutes' AS retry FROM case_jobs WHERE job_id='older'");
    expect(window.rows[0].retry).toBe(true);
    await db.query("UPDATE case_jobs SET snapshot_alert_after=NOW()-interval '1 second' WHERE job_id='older'");
    notify.mockResolvedValue(true);
    await notifySnapshotWaits(db, notify, 'test');
    const sent = await db.query("SELECT snapshot_alert_after > NOW()+interval '59 minutes' AS throttled FROM case_jobs WHERE job_id='older'");
    expect(sent.rows[0].throttled).toBe(true);
    expect(notify).toHaveBeenCalledTimes(2);
  });
});
