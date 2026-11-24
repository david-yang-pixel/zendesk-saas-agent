import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { expect, it, vi } from 'vitest';
import { publicDeliveryIdsInBody } from './relay.js';

const external = vi.hoisted(() => ({
  events: [] as Record<string, unknown>[],
  directoryRefreshes: [] as unknown[],
  runAgent: vi.fn(async ({ customerMessage }: { customerMessage: string }) => ({
    reply: `Answer to ${customerMessage}`,
    trace: { toolCalls: [], finalReply: `Answer to ${customerMessage}` },
  })),
}));

vi.mock('./agent.js', () => ({ runPiSupportAgent: external.runAgent }));
vi.mock('./openconnector.js', () => ({
  createOpenConnectorServiceConfig: () => ({ name: 'openconnector-test' }),
  startReadToolDirectoryRefresh: (config: unknown) => { external.directoryRefreshes.push(config); return () => {}; },
  describeReadToolDirectory: async () => 'DIRECTORY-SNAPSHOT',
  discoverOpenConnectorTools: async () => [{ name: 'read' }],
  callOpenConnectorCustomMcpTool: async (_service: unknown, _connection: string, operation: string) => {
    if (operation === 'get_ticket') return { data: { ticket: { subject: 'Test ticket' } } };
    if (operation === 'get_ticket_conversation_log') return { data: { events: [...external.events], meta: { has_more: false } } };
    throw new Error(`Unexpected test read operation ${operation}`);
  },
  parseOpenConnectorResult: (result: unknown) => result,
}));

const testDatabase = process.env.TEST_DATABASE_URL?.replace(/^postgresql\+\w+:\/\//, 'postgresql://');

it.skipIf(!testDatabase)('worker entry recovers a cold pending write and keeps #5691 follow-up coverage exact', async () => {
  const schema = `worker_delivery_${randomUUID().replace(/-/g, '')}`;
  const admin = new pg.Pool({ connectionString: testDatabase });
  const dbUrl = new URL(testDatabase!);
  dbUrl.searchParams.set('options', `-c search_path=${schema}`);
  const db = new pg.Pool({ connectionString: dbUrl.toString() });
  const ticketId = 5691;
  let stopWorker: (() => Promise<void>) | undefined;
  const outbound: { ticket_id: number; body: string }[] = [];
  const alerts: unknown[] = [];
  let postedAt: string;
  const row = async (table: 'case_jobs' | 'public_deliveries' | 'run_records', jobId: string) =>
    (await db.query(`SELECT * FROM ${table} WHERE job_id = $1`, [jobId])).rows[0];
  const waitFor = (verify: () => Promise<void>) => vi.waitFor(verify, { timeout: 8_000, interval: 50 });

  async function startWorker() {
    const before = new Set(process.listeners('SIGTERM'));
    vi.resetModules();
    const { workerCompletion } = await import('./worker.js');
    const added = () => process.listeners('SIGTERM').find(listener => !before.has(listener));
    await waitFor(async () => { expect(added()).toBeDefined(); });
    const shutdown = added()!;
    return async () => {
      shutdown('SIGTERM');
      await workerCompletion;
      process.off('SIGTERM', shutdown);
      process.off('SIGINT', shutdown);
    };
  }

  async function enqueue(cursor: string, sequence: number) {
    const jobId = randomUUID();
    await db.query(`
      INSERT INTO events (event_id, ticket_id, occurred_at, source, customer_origin, event_type, message_id, customer_seq, payload)
      VALUES ($1, $2, NOW(), 'test', 'customer', 'ticket.updated', $3, $4, '{}')
    `, [jobId, ticketId, cursor, sequence]);
    await db.query('UPDATE ticket_states SET latest_customer_seq = $2 WHERE ticket_id = $1', [ticketId, sequence]);
    await db.query(`
      INSERT INTO case_jobs (job_id, session_id, event_id, expected_event_id, ticket_id, generation, status,
        config_revision, root_model_config_id, child_model_config_ids, model_digest, skill_digest, tool_digest)
      VALUES ($1, $1, $1, $2, $3, 1, 'queued', 'test', 'test', '[]', 'test', 'test', 'test')
    `, [jobId, cursor, ticketId]);
    return jobId;
  }

  const customer = (id: string, text: string, time: Date) => ({
    id: `history-${id}`, reference: `zen:sunco:conversation_message:${id}`, created_at: time.toISOString(),
    author: { type: 'end-user', id: 'the-customer' }, content: { text },
  });

  try {
    await admin.query(`CREATE SCHEMA ${schema}`);
    for (const table of ['ticket_states', 'events', 'case_jobs', 'run_records', 'public_deliveries', 'model_endpoint_cooldowns', 'human_handoffs']) {
      await db.query(`CREATE TABLE ${table} (LIKE public.${table} INCLUDING ALL)`);
    }
    await db.query(`ALTER TABLE case_jobs ADD FOREIGN KEY (event_id) REFERENCES events(event_id);
      ALTER TABLE public_deliveries ADD FOREIGN KEY (job_id) REFERENCES case_jobs(job_id);
      ALTER TABLE run_records ADD FOREIGN KEY (job_id) REFERENCES case_jobs(job_id);
      CREATE TABLE system_settings (key text PRIMARY KEY, value jsonb NOT NULL);
      INSERT INTO system_settings VALUES ('model_config', '{"base_url":"https://model.example/v1","model":"test","api_key":"test"}');
      INSERT INTO ticket_states (ticket_id) VALUES (5691);`);
    vi.stubEnv('DATABASE_URL', dbUrl.toString());
    vi.stubEnv('SERVICE_PORT', '0');
    vi.stubEnv('CASEWORK_CREDENTIAL_OPENCONNECTOR_RUNTIME', 'test');
    vi.stubEnv('CASEWORK_BROWSER_RELAY_URL', 'https://relay.example');
    vi.stubEnv('FEISHU_WEBHOOK_URL', 'https://alerts.example');
    vi.stubEnv('CASEWORK_WORKER_CONCURRENCY', '1');
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (url === 'https://alerts.example') {
        alerts.push(JSON.parse(String(init.body)));
        return Response.json({ code: 0 });
      }
      if (url !== 'https://relay.example/send') throw new Error(`Unexpected fetch ${url}`);
      outbound.push(JSON.parse(String(init.body)));
      if (outbound.length === 1) {
        postedAt = new Date().toISOString();
        throw new Error('Relay socket reset after browser submission');
      }
      return Response.json({ ok: true, status: 'sent', ticket_id: ticketId });
    }));
    external.events = [{ id: 'form-sent', created_at: new Date(Date.now() - 10_000).toISOString(),
      author: { type: 'system' }, content: { type: 'form', fields: [] } }];
    const first = await enqueue('messaging:original', 1);
    await db.query(`CREATE FUNCTION fail_delivery_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF OLD.status = 'pending' AND NEW.status IN ('sent', 'uncertain') THEN
        RAISE EXCEPTION 'simulated local database write failure after relay attempt';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER fail_delivery_write BEFORE UPDATE ON public_deliveries FOR EACH ROW EXECUTE FUNCTION fail_delivery_write();`);

    stopWorker = await startWorker();
    await waitFor(async () => expect(await row('case_jobs', first)).toMatchObject({
      status: 'queued', attempts: 0, dead_letter_reason: 'waiting_for_conversation_snapshot',
    }));
    expect(external.runAgent).not.toHaveBeenCalled();
    expect(outbound).toHaveLength(0);

    const submittedForm = 'Name: Alex\nEmail: alex@example.test\nWorkspace ID: example';
    const modelReply = 'Welcome, Alex! What would you like a hand with?';
    external.runAgent.mockResolvedValueOnce({ reply: modelReply, trace: { toolCalls: [], finalReply: modelReply } });
    external.events.push({ ...customer('original', '', new Date(Date.now() - 5_000)),
      content: { type: 'formResponse', fields: [], text_fallback: submittedForm } });
    await db.query('UPDATE case_jobs SET available_at = NOW() WHERE job_id = $1', [first]);
    await waitFor(async () => {
      expect(outbound).toHaveLength(1);
      expect(await row('public_deliveries', first)).toMatchObject({ status: 'pending', started_at: expect.any(Date) });
    });
    const firstPermit = await row('public_deliveries', first);
    expect(external.runAgent).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      customerMessage: submittedForm, conversationContext: expect.stringContaining(submittedForm),
    }));
    expect(outbound[0].body).toContain(modelReply);
    expect(publicDeliveryIdsInBody(outbound[0].body)).toEqual([firstPermit.delivery_id]);
    const followUp = await enqueue('messaging:follow-up', 2);
    await stopWorker();
    stopWorker = undefined;

    await db.query('DROP TRIGGER fail_delivery_write ON public_deliveries');
    await db.query('UPDATE public_deliveries SET next_check_at = NOW() WHERE job_id = $1', [first]);
    external.events.push(customer('follow-up', 'Still broken.', new Date(Date.parse(postedAt!) - 200)));
    stopWorker = await startWorker();
    await waitFor(async () => expect(await row('public_deliveries', first)).toMatchObject({
      status: 'uncertain', alert_sent_at: expect.any(Date),
    }));
    expect(await row('case_jobs', followUp)).toMatchObject({ status: 'queued' });
    expect(alerts).toHaveLength(1);
    expect(JSON.stringify(alerts)).toContain(firstPermit.delivery_id);
    expect(JSON.stringify(alerts)).not.toContain('Welcome, Alex!');
    expect(outbound).toHaveLength(1);

    external.events.push({ id: 'verified-remote-message', created_at: postedAt!, public: true,
      author: { type: 'agent', id: 'browser-relay-operator' }, content: { text: outbound[0].body } });
    await db.query('UPDATE public_deliveries SET next_check_at = NOW() WHERE job_id = $1', [first]);
    await waitFor(async () => expect(await row('public_deliveries', followUp)).toMatchObject({ status: 'sent' }));
    expect(outbound).toHaveLength(2);
    expect(outbound[1].body).toMatch(/^Answer to Still broken\./);
    expect(outbound[1].body).not.toContain(submittedForm);
    expect(external.directoryRefreshes).toEqual([{ name: 'openconnector-test' }, { name: 'openconnector-test' }]);
    expect(external.runAgent).toHaveBeenCalledWith(expect.objectContaining({ toolDirectory: 'DIRECTORY-SNAPSHOT' }));
    expect(external.runAgent).toHaveBeenCalledTimes(2);
    expect(external.runAgent).toHaveBeenLastCalledWith(expect.objectContaining({ customerMessage: 'Still broken.' }));
    expect(await row('run_records', first)).toMatchObject({ status: 'completed', outcome: { delivery: {
      status: 'sent', source: 'zendesk-conversation', message_id: 'verified-remote-message', delivery_id: firstPermit.delivery_id,
    } } });
    expect((await db.query('SELECT message_id, answered_by_job_id FROM events WHERE customer_seq IS NOT NULL ORDER BY customer_seq')).rows)
      .toEqual([{ message_id: 'messaging:original', answered_by_job_id: first },
        { message_id: 'messaging:follow-up', answered_by_job_id: followUp }]);
  } finally {
    await stopWorker?.();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    await db.end();
    await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
    await admin.end();
  }
}, 30_000);
