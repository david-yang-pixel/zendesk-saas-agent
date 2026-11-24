import type { McpServiceConfig } from './mcp.js';
import type { AgentAttachment } from './agent.js';
import { callOpenConnectorCustomMcpTool, parseOpenConnectorResult } from './openconnector.js';
import { detectHumanHandoffIntent } from './intent.js';
import { loadConversation } from './conversation.js';
import { publicDeliveryIdsInBody } from './relay.js';

function identifier(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  return typeof value === 'number' && Number.isSafeInteger(value) ? String(value) : '';
}

const SUNCO_REFERENCE = 'zen:sunco:conversation_message:';
const COMMENT_REFERENCE = 'zen:ticket:comment:';
const CUSTOMER_TYPES = /^(?:end[-_]?user|customer|requester|user)$/;
const STAFF_TYPES = /^(?:agent|admin|staff)$/;
const AI_AGENT_TYPES = /^ai[-_]?agent$/;
const QUOTED_REPLY_FOOTER = /(?:^|\n\s*---\s*\n)(?:This reply was sent by Dify AI Agent|此回复由 Dify AI Agent 发送|この返信は Dify AI Agent が送信しました|Dify AI Agent envió esta respuesta)[\s\S]*$/i;

/** Zendesk's conversation event ID and the SunCo message ID are different namespaces. */
export function customerEventCursors(ev: { id?: unknown; reference?: unknown }): string[] {
  const cursors = new Set<string>();
  const add = (namespace: string, id: string) => {
    if (id && !id.includes(':') && namespace.length + id.length <= 255) cursors.add(`${namespace}${id}`);
  };
  const reference = identifier(ev.reference);
  const id = identifier(ev.id);
  if (reference.startsWith(SUNCO_REFERENCE)) add('messaging:', reference.slice(SUNCO_REFERENCE.length));
  if (id.startsWith(SUNCO_REFERENCE)) add('messaging:', id.slice(SUNCO_REFERENCE.length));
  if (id.startsWith('messaging:')) add('messaging:', id.slice('messaging:'.length));
  if (reference.startsWith(COMMENT_REFERENCE)) add('zendesk:', reference.slice(COMMENT_REFERENCE.length));
  if (id.startsWith(COMMENT_REFERENCE)) add('zendesk:', id.slice(COMMENT_REFERENCE.length));
  add('zendesk:', id.startsWith('zendesk:') ? id.slice('zendesk:'.length) : id);
  return [...cursors];
}

function isContactFormOnly(text: string): boolean {
  const fields = new Set<string>();
  for (const line of text.split(/\r?\n/).map(value => value.trim()).filter(Boolean)) {
    const match = /^(name|email|workspace id|plan)\s*:\s*(.+)$/i.exec(line);
    if (!match) return false;
    const field = match[1].toLowerCase();
    if (fields.has(field) || (field === 'email' && !/^[^\s@]+@[^\s@]+$/.test(match[2]))) return false;
    fields.add(field);
  }
  return fields.has('name') && fields.has('email');
}

function extractEventDetails(ev: any) {
  const content = ev.content;
  const structuredForm = /^(?:form|form[_-]?response)$/i.test(content?.type ?? ev.type ?? '') ||
    (Array.isArray(content?.fields) && !content.text && !content.body);
  const candidates = typeof content === 'string'
    ? [content, ev.body, ev.message]
    : [content?.text, content?.body, ev.body, ev.body?.text, ev.message, ev.message?.text];
  const text = candidates.find(value => typeof value === 'string' && value.trim())?.trim() ?? '';
  const fallback = typeof content?.text_fallback === 'string' ? content.text_fallback.trim() : '';
  const form = (structuredForm && !text) || isContactFormOnly(text || fallback);
  const attachments: AgentAttachment[] = [];

  function addAttachment(attachment: any, url: unknown) {
    if (typeof url !== 'string' || !url.trim()) return;
    attachments.push({
      id: identifier(attachment.id ?? attachment.attachment_id) || url,
      name: String(attachment.alt_text || attachment.file_name || attachment.name || 'attachment'),
      url,
      contentType: attachment.media_type || attachment.content_type || attachment.contentType ||
        (/\.pdf(?:$|\?)/i.test(url) ? 'application/pdf' :
          attachment.type === 'image' ? 'image/png' : undefined),
    });
  }

  if (content && typeof content === 'object') {
    const mediaUrl = content.media_url || content.mediaUrl ||
      (['image', 'file', 'audio', 'video'].includes(content.type) ? content.url || content.content_url || content.contentUrl : undefined);
    addAttachment(content, mediaUrl);
  }
  for (const list of [content?.attachments, ev.attachments]) {
    if (!Array.isArray(list)) continue;
    for (const attachment of list) {
      if (attachment && typeof attachment === 'object') {
        addAttachment(attachment, attachment.content_url || attachment.contentUrl || attachment.url || attachment.media_url || attachment.mediaUrl);
      }
    }
  }
  // HTML comments can carry an image and no text.
  if (content?.type === 'html') {
    for (const image of text.matchAll(/<img\b[^>]*\bsrc\s*=\s*["'](https?:\/\/[^"']+)["']/gi)) {
      addAttachment({ type: 'image', name: 'inline image' }, image[1]);
    }
  }
  const issueText = content?.type === 'html' && !text.replace(/<[^>]*>|&nbsp;|&#160;/gi, '').trim()
    ? '' : text;
  const rendered = text || fallback || (form && content?.fields?.length ? JSON.stringify(content.fields) : '');
  return { text: form ? rendered : issueText || (content?.type === 'html' ? '' : fallback), historyText: rendered, attachments, form };
}

export interface ReadCustomerMessage {
  cursors: string[];
  occurredAt?: string;
}

interface CustomerMessage extends ReadCustomerMessage {
  formOnly: boolean;
  id: string;
  text: string;
  attachments: AgentAttachment[];
  requestsHuman: boolean;
}

interface ConversationCoverage {
  pendingCursors: ReadonlySet<string>;
  coveredCursors: ReadonlySet<string>;
}

export interface HandoffResume {
  occurredAt: string | Date;
  /** The signed Zendesk internal note cursor, in the mention:<comment ID> namespace. */
  cursor: string | null;
}

export interface TicketContextOptions {
  handoffResume?: HandoffResume | null;
  /** Provider cursors for confirmed automated public deliveries on this ticket. */
  automatedReplyCursors?: ReadonlySet<string>;
  confirmedDeliveryIds?: ReadonlySet<string>;
  /** Stable provider author IDs for accounts used exclusively by automation. */
  automationAuthorIds?: ReadonlySet<string>;
}

function isInternalEvent(ev: any): boolean {
  return ev.public === false || ev.is_public === false || ev.metadata?.is_public === false;
}

function resumeNoteIndex(events: any[], resume: HandoffResume | null | undefined): number {
  if (!resume?.cursor?.startsWith('mention:')) return -1;
  const commentId = resume.cursor.slice('mention:'.length);
  return events.findIndex(ev => ev && isInternalEvent(ev) && (
    identifier(ev.reference) === `${COMMENT_REFERENCE}${commentId}` ||
    identifier(ev.id) === `${COMMENT_REFERENCE}${commentId}` ||
    identifier(ev.id) === commentId
  ));
}

interface TicketDetails {
  message: string;
  conversationContext: string;
  email?: string;
  attachments: AgentAttachment[];
  lastEventId?: string;
  lastEventTime?: string;
  customerEventsCount: number;
  hasRequestedHuman: boolean;
  latestRequestedHuman: boolean;
  humanRequestOccurredAt?: string;
  humanRequestMessage?: string;
  isAlreadyAnswered: boolean;
  unrepliedCount: number;
  contactFormOnly: boolean;
  /** The substantive customer messages actually included in the model input. */
  readCustomerMessages: ReadCustomerMessage[];
  visibleCustomerCursors: Set<string>;
  staffAnsweredCursors: string[];
}

export async function getTicketDetails(
  ticketId: number, openConnector: McpServiceConfig, signal?: AbortSignal, coverage?: ConversationCoverage,
  options: TicketContextOptions = {},
): Promise<TicketDetails> {
  try {
    // Metadata is optional and independent of the conversation, so both reads
    // run together; a metadata failure never blocks the conversation.
    const metadata = callOpenConnectorCustomMcpTool(
      openConnector, 'zendesk', 'get_ticket', { ticket_id: ticketId }, signal,
    ).then(rawTicket => {
      const ticket = parseOpenConnectorResult(rawTicket)?.data?.ticket;
      return { email: ticket?.requester?.email || ticket?.email || '', subject: ticket?.subject || '' };
    }).catch((error: any) => {
      console.warn('[Worker] Could not fetch ticket metadata:', error.message);
      return { email: '', subject: '' };
    });
    const [allEvents, ticketMetadata] = await Promise.all([
      loadConversation(ticketId, openConnector, signal),
      metadata,
    ]);
    let email = ticketMetadata.email;
    const subject = ticketMetadata.subject;
    if (allEvents.every(event => Number.isFinite(Date.parse(event?.created_at)))) {
      allEvents.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
    }
    const resume = options.handoffResume;
    const resumeTime = resume?.occurredAt instanceof Date ? resume.occurredAt.getTime() : Date.parse(resume?.occurredAt ?? '');
    const noteIndex = resumeNoteIndex(allEvents, resume);
    const allAttachments: AgentAttachment[] = [];
    const history: string[] = [];
    const visibleCustomerCursors = new Set<string>();
    const customerMessages: CustomerMessage[] = [];
    let lastReplyIndex = 0;
    let lastStaffReplyIndex = 0;
    let foundAgentReply = false;
    let latestReplyAllowsPending = false;
    let hasRequestedHuman = false;
    let humanRequestOccurredAt: string | undefined;

    for (const [index, ev] of allEvents.entries()) {
      if (!ev || isInternalEvent(ev)) continue;
      const authorType = (identifier(ev.author?.type) || identifier(ev.author?.role)).toLowerCase();
      const isCustomer = CUSTOMER_TYPES.test(authorType);
      const isAgent = STAFF_TYPES.test(authorType) || AI_AGENT_TYPES.test(authorType);
      if (!isAgent && !isCustomer) continue;
      const { text, historyText, attachments, form } = extractEventDetails(ev);
      if (isAgent && !form && (text || attachments.length)) {
        foundAgentReply = true;
        lastReplyIndex = customerMessages.length;
        const authorId = identifier(ev.author?.id ?? ev.author?.user_id ?? ev.author_id);
        const isAutomated = AI_AGENT_TYPES.test(authorType) ||
          options.automationAuthorIds?.has(authorId) ||
          publicDeliveryIdsInBody(historyText).some(id => options.confirmedDeliveryIds?.has(id)) ||
          customerEventCursors(ev).some(cursor => options.automatedReplyCursors?.has(cursor));
        // An unidentifiable legacy support reply is visible as a reply, but cannot
        // settle an exact pending message permanently or hide it from this run.
        latestReplyAllowsPending = Boolean(isAutomated) || !authorId;
        if (!latestReplyAllowsPending) lastStaffReplyIndex = customerMessages.length;
      }
      if (isCustomer) {
        allAttachments.push(...attachments);
        if (!email && Array.isArray(ev.content?.fields)) {
          email = ev.content.fields.find((field: any) => field.type === 'EMAIL')?.email || '';
        }
        const cursors = customerEventCursors(ev);
        cursors.forEach(cursor => visibleCustomerCursors.add(cursor));
        if (form || text || attachments.length) {
          const occurredAt = typeof ev.created_at === 'string' ? ev.created_at : undefined;
          const occurredTime = Date.parse(occurredAt ?? '');
          const afterResume = Number.isFinite(resumeTime) && Number.isFinite(occurredTime) && occurredTime !== resumeTime
            ? occurredTime > resumeTime
            : noteIndex === -1 || index > noteIndex;
          const requestsHuman = !form && afterResume && detectHumanHandoffIntent(text.replace(QUOTED_REPLY_FOOTER, ''));
          if (requestsHuman && Number.isFinite(occurredTime) &&
              (!humanRequestOccurredAt || occurredTime > Date.parse(humanRequestOccurredAt))) {
            humanRequestOccurredAt = occurredAt;
          }
          customerMessages.push({
            id: identifier(ev.id) || identifier(ev.reference), text, attachments, cursors,
            formOnly: form && attachments.length === 0,
            occurredAt, requestsHuman,
          });
          hasRequestedHuman ||= requestsHuman;
        }
      }
      if (historyText || attachments.length) history.push(
        `${isAgent ? 'Support' : 'Customer'} [${ev.created_at || 'time unknown'}]:\n${historyText.slice(0, 4000)}${historyText.length > 4000 ? '\n[Message truncated; fetch this event if needed.]' : ''}` +
        attachments.map(a => `\n[Attachment: ${a.name} (${a.contentType || 'file'}), URL: ${a.url || 'N/A'}]`).join(''),
      );
    }

    // An older AI run may have posted after an admitted follow-up appeared.
    // An exact pending cursor can recover that follow-up from before the AI reply.
    const unreplied = customerMessages.filter((message, index) => {
      if (message.cursors.some(cursor => coverage?.coveredCursors.has(cursor))) return false;
      return index >= lastReplyIndex ||
        (latestReplyAllowsPending && index >= lastStaffReplyIndex && message.cursors.some(cursor => coverage?.pendingCursors.has(cursor)));
    });
    const customerMessage = unreplied.map(message => [message.text, ...message.attachments.map(a =>
      `[Attachment: ${a.name} (${a.contentType || 'file'}), URL: ${a.url || 'N/A'}]`,
    )].filter(Boolean).join('\n')).filter(Boolean).join('\n\n');
    const latest = unreplied.at(-1);
    const contactFormOnly = unreplied.length > 0 && unreplied.every(message => message.formOnly);

    const selected = history.length > 25
      ? [...history.slice(0, 2), `[${history.length - 25} older public messages omitted; fetch older history if material.]`, ...history.slice(-23)]
      : history;
    const boundedHistory: string[] = [];
    let remaining = 36_000;
    for (let i = selected.length - 1; i >= 0; i--) {
      if (selected[i].length > remaining) {
        boundedHistory.unshift('[Earlier public context omitted due to size; fetch it if material.]');
        break;
      }
      boundedHistory.unshift(selected[i]);
      remaining -= selected[i].length;
    }

    return {
      message: customerMessage || subject || `Ticket #${ticketId}`,
      conversationContext: boundedHistory.join('\n\n'),
      email,
      attachments: [...new Map(allAttachments.map(attachment => [attachment.id, attachment])).values()].slice(-8),
      lastEventId: latest?.id,
      lastEventTime: latest?.occurredAt,
      customerEventsCount: unreplied.length,
      hasRequestedHuman,
      latestRequestedHuman: unreplied.some(message => message.requestsHuman),
      humanRequestOccurredAt,
      humanRequestMessage: customerMessages.filter(message => message.requestsHuman).at(-1)?.text,
      isAlreadyAnswered: unreplied.length === 0 && foundAgentReply,
      unrepliedCount: unreplied.length,
      contactFormOnly,
      readCustomerMessages: unreplied.map(({ cursors, occurredAt }) => ({ cursors, occurredAt })),
      visibleCustomerCursors,
      staffAnsweredCursors: customerMessages.slice(0, lastStaffReplyIndex)
        .flatMap(message => message.cursors).filter(cursor => coverage?.pendingCursors.has(cursor)),
    };
  } catch (error: any) {
    throw new Error(`Unable to load complete ticket context for #${ticketId}: ${error.message}`);
  }
}
