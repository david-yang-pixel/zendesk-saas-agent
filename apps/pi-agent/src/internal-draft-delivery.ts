import type pg from 'pg';
import { writtenLanguage } from './reply-policy.js';

export const INTERNAL_DRAFT_MARKER = '[DIFY_AI_AGENT_DRAFT:';

export interface InternalDraftDelivery {
  channel: 'zendesk-internal-note';
  status: 'sent' | 'already_sent';
}

/**
 * The note text after the marker line. With an operator answer the note leads
 * with it, in the operator's language, followed by the unsent customer draft.
 */
export function formatInternalDraft(customerReply: string, operatorAnswer?: string): string {
  const draft = customerReply.trim().replace(
    /\n\s*---\s*\nThis reply was sent by Dify AI Agent[\s\S]*$/i,
    '',
  ).trim();
  if (!draft) throw new Error('Internal customer-reply draft is empty or too long');
  const answer = operatorAnswer?.trim();
  if (!answer) return `AI-generated customer reply draft for human review. Not sent to the customer.\n\n${draft}`;
  return writtenLanguage(answer) === 'zh'
    ? `AI 调查结论（仅内部可见）\n\n${answer}\n\n---\n客户回复草稿（英文，未发送给客户）：\n\n${draft}`
    : `AI investigation answer (internal only)\n\n${answer}\n\n---\nCustomer reply draft (not sent to the customer):\n\n${draft}`;
}

export function buildInternalDraftBody(jobId: string, customerReply: string, operatorAnswer?: string): string {
  if (!/^[a-zA-Z0-9_-]{1,255}$/.test(jobId)) {
    throw new Error('Invalid internal draft job ID');
  }
  const note = formatInternalDraft(customerReply, operatorAnswer);
  if (note.length > 20000) {
    throw new Error('Internal customer-reply draft is empty or too long');
  }
  return `${INTERNAL_DRAFT_MARKER}${jobId}]\n${note}`;
}

function zendeskTicketUrl(baseUrl: string, ticketId: number): string {
  const origin = new URL(baseUrl);
  if (origin.protocol !== 'https:' || !origin.hostname.endsWith('.zendesk.com') ||
      origin.username || origin.password || origin.port || origin.pathname !== '/' ||
      origin.search || origin.hash || !Number.isSafeInteger(ticketId) || ticketId <= 0) {
    throw new Error('Invalid Zendesk internal draft destination');
  }
  return new URL(`/api/v2/tickets/${ticketId}.json`, origin).toString();
}

export async function deliverInternalDraft(options: {
  pool: Pick<pg.Pool, 'query'>;
  jobId: string;
  ticketId: number;
  customerReply: string;
  operatorAnswer?: string;
  baseUrl: string;
  email: string;
  apiToken: string;
  fetchImpl?: typeof fetch;
}): Promise<InternalDraftDelivery> {
  const body = buildInternalDraftBody(options.jobId, options.customerReply, options.operatorAnswer);
  const url = zendeskTicketUrl(options.baseUrl, options.ticketId);
  if (!options.email.trim() || !options.apiToken.trim()) {
    throw new Error('Zendesk internal draft credentials are missing');
  }

  // The fence is committed before the external write. An expired worker lease
  // cannot resend the same draft after a crash or an ambiguous HTTP timeout.
  const claimed = await options.pool.query(
    `INSERT INTO internal_draft_deliveries (job_id, ticket_id, status)
     VALUES ($1, $2, 'attempting') ON CONFLICT (job_id) DO NOTHING RETURNING job_id`,
    [options.jobId, options.ticketId],
  );
  if (claimed.rowCount === 0) {
    const prior = await options.pool.query(
      'SELECT ticket_id, status FROM internal_draft_deliveries WHERE job_id = $1',
      [options.jobId],
    );
    if (Number(prior.rows[0]?.ticket_id) !== options.ticketId) {
      throw new Error('Internal draft delivery fence ticket mismatch');
    }
    if (prior.rows[0]?.status === 'sent') {
      return { channel: 'zendesk-internal-note', status: 'already_sent' };
    }
    throw new Error('Internal draft delivery is uncertain; manual review required before retry');
  }

  const authorization = Buffer.from(`${options.email.trim()}/token:${options.apiToken.trim()}`).toString('base64');
  let response: Response;
  try {
    response = await (options.fetchImpl ?? fetch)(url, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Basic ${authorization}`,
      },
      body: JSON.stringify({ ticket: { comment: { body, public: false } } }),
      signal: AbortSignal.timeout(15000),
      redirect: 'error',
    });
  } catch {
    // Zendesk may have accepted the comment even if the response was lost.
    throw new Error('Zendesk internal draft result is uncertain; manual review required');
  }
  if (!response.ok) {
    // Never log response content: it may contain customer data or credentials.
    throw new Error(`Zendesk internal draft request failed with HTTP ${response.status}; manual review required`);
  }

  const saved = await options.pool.query(
    `UPDATE internal_draft_deliveries SET status = 'sent', sent_at = NOW()
     WHERE job_id = $1 AND ticket_id = $2 AND status = 'attempting'`,
    [options.jobId, options.ticketId],
  );
  if (saved.rowCount !== 1) {
    throw new Error('Zendesk internal draft may have been sent but its delivery receipt was not saved');
  }
  return { channel: 'zendesk-internal-note', status: 'sent' };
}
