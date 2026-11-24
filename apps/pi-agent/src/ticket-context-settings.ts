import type pg from 'pg';
import type { TicketContextOptions } from './ticket-context.js';

/** Use one snapshot for both the resume boundary and trusted automated reply identities. */
export async function ticketContextSettings(db: Pick<pg.Pool, 'query'>, ticketId: number): Promise<TicketContextOptions> {
  const { rows } = await db.query<{
    resumed_at: string | null; human_resume_cursor: string | null; delivery_id: string | null;
    message_id: string | null;
  }>(`
    SELECT s.human_resumed_at::text AS resumed_at, s.human_resume_cursor, d.delivery_id,
           d.receipt->>'message_id' AS message_id
    FROM ticket_states s LEFT JOIN public_deliveries d ON d.ticket_id=s.ticket_id AND d.status='sent'
    WHERE s.ticket_id=$1
  `, [ticketId]);
  const state = rows[0];
  return {
    handoffResume: state?.resumed_at ? { occurredAt: state.resumed_at, cursor: state.human_resume_cursor } : null,
    automatedReplyCursors: new Set(rows.flatMap(row => row.message_id ? [`zendesk:${row.message_id}`] : [])),
    confirmedDeliveryIds: new Set(rows.flatMap(row => row.delivery_id ? [row.delivery_id] : [])),
    automationAuthorIds: new Set((process.env.CASEWORK_AUTOMATION_AUTHOR_IDS ?? '').split(',').map(id => id.trim()).filter(Boolean)),
  };
}
