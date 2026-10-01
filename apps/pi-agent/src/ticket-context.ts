import type { McpServiceConfig } from './mcp.js';
import type { AgentAttachment } from './agent.js';
import { callOpenConnectorCustomMcpTool, parseOpenConnectorResult } from './openconnector.js';
import { detectHumanHandoffIntent } from './intent.js';
import { loadConversation } from './conversation.js';

interface EventExtraction {
  text: string;
  attachments: AgentAttachment[];
}

function extractEventDetails(ev: any): EventExtraction {
  let text = '';
  const attachments: AgentAttachment[] = [];
  if (!ev) return { text: '', attachments: [] };

  if (typeof ev.content === 'string' && ev.content.trim()) {
    text = ev.content.trim();
  } else if (ev.content && typeof ev.content === 'object') {
    if (typeof ev.content.text === 'string' && ev.content.text.trim()) text = ev.content.text.trim();
    else if (typeof ev.content.body === 'string' && ev.content.body.trim()) text = ev.content.body.trim();
    else if (typeof ev.content.text_fallback === 'string' && ev.content.text_fallback.trim()) text = ev.content.text_fallback.trim();

    if (ev.content.media_url || ev.content.url) {
      attachments.push({
        id: String(ev.content.attachment_id || ev.id || `${Date.now()}`),
        name: String(ev.content.alt_text || ev.content.file_name || ev.content.name || 'image_attachment'),
        url: ev.content.media_url || ev.content.url,
        contentType: ev.content.media_type || ev.content.content_type ||
          (/\.pdf(?:$|\?)/i.test(ev.content.media_url || ev.content.url) ? 'application/pdf' :
            ev.content.type === 'image' ? 'image/png' : undefined),
      });
    }

    if (Array.isArray(ev.content.attachments)) {
      for (const a of ev.content.attachments) {
        if (a && typeof a === 'object') {
          attachments.push({
            id: String(a.id || a.attachment_id || `${Date.now()}`),
            name: String(a.file_name || a.name || 'attachment'),
            url: a.content_url || a.url,
            contentType: a.content_type,
          });
        }
      }
    }
  }

  if (!text) {
    if (typeof ev.body === 'string' && ev.body.trim()) text = ev.body.trim();
    else if (typeof ev.message === 'string' && ev.message.trim()) text = ev.message.trim();
    else if (ev.body && typeof ev.body === 'object' && typeof ev.body.text === 'string' && ev.body.text.trim()) {
      text = ev.body.text.trim();
    } else if (ev.message && typeof ev.message === 'object' && typeof ev.message.text === 'string' && ev.message.text.trim()) {
      text = ev.message.text.trim();
    }
  }

  if (Array.isArray(ev.attachments)) {
    for (const a of ev.attachments) {
      if (a && typeof a === 'object') {
        attachments.push({
          id: String(a.id || a.attachment_id || `${Date.now()}`),
          name: String(a.file_name || a.name || 'attachment'),
          url: a.content_url || a.url,
          contentType: a.content_type,
        });
      }
    }
  }

  return { text, attachments };
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
  isAlreadyAnswered: boolean;
  unrepliedCount: number;
}

export async function getTicketDetails(ticketId: number, openConnector: McpServiceConfig, signal?: AbortSignal): Promise<TicketDetails> {
  try {
    let email = '';
    let subject = '';

    try {
      const rawTicket = await callOpenConnectorCustomMcpTool(
        openConnector,
        'zendesk',
        'get_ticket',
        { ticket_id: ticketId },
        signal,
      );
      {
        const parsed = parseOpenConnectorResult(rawTicket);
        email = parsed?.data?.ticket?.requester?.email || parsed?.data?.ticket?.email || '';
        subject = parsed?.data?.ticket?.subject || '';
      }
    } catch (e: any) {
      console.warn(`[Worker] Could not fetch ticket metadata:`, e.message);
    }

    let allEvents: any[];
    try {
      allEvents = await loadConversation(ticketId, openConnector, signal);
    } catch (e: any) {
      throw new Error(`Could not fetch Zendesk conversation log for ticket #${ticketId}: ${e.message}`);
    }

    // Collect unreplied customer messages since the last agent message
    const unrepliedCustomerMessages: Array<{ id: string; text: string; time: string; attachments: AgentAttachment[] }> = [];
    const allAttachments: AgentAttachment[] = [];
    const history: string[] = [];
    let hasRequestedHuman = false;
    let isAlreadyAnswered = false;

    // Give each fresh model session the public thread, including prior answers
    // and attachment metadata. Internal operator notes stay out of this context.
    for (const ev of allEvents) {
      if (ev.public === false || ev.metadata?.is_public === false || ev.author?.type === 'system') continue;
      const { text, attachments } = extractEventDetails(ev);
      const isAgent = ev.author?.type === 'agent' || /David Yang|AI Agent/.test(ev.author?.display_name || '');
      if (!isAgent) {
        allAttachments.push(...attachments);
        if (!email) {
          const fields = ev.content?.fields;
          if (Array.isArray(fields)) email = fields.find((field: any) => field.type === 'EMAIL')?.email || '';
        }
      }
      if (text || attachments.length) history.push(
        `${isAgent ? 'Support' : 'Customer'} [${ev.created_at || 'time unknown'}]:\n${text.slice(0, 4000)}${text.length > 4000 ? '\n[Message truncated; fetch this event if needed.]' : ''}` +
        attachments.map(a => `\n[Attachment: ${a.name} (${a.contentType || 'file'}), URL: ${a.url || 'N/A'}]`).join(''),
      );
    }
    // Retain initial identity/question plus recent turns, and disclose omissions.
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

    // 1. Check if the customer EVER asked for human support in any message using refined intent detection
    for (const ev of allEvents) {
      if (ev.public === false || ev.metadata?.is_public === false) continue;
      const authorName = ev.author?.display_name || '';
      const authorType = ev.author?.type || '';
      const isAgent = authorType === 'agent' || authorName.includes('David Yang') || authorName.includes('AI Agent');
      if (!isAgent) {
        const { text } = extractEventDetails(ev);
        if (detectHumanHandoffIntent(text)) {
          hasRequestedHuman = true;
          break;
        }
      }
    }

    // 2. Scan backwards from newest to oldest:
    // If the latest event in the conversation is an agent reply, older customer messages have already been replied!
    let foundAgentReply = false;
    let latestEventId = '';
    let latestEventTime = '';

    for (let i = allEvents.length - 1; i >= 0; i--) {
      const ev = allEvents[i];
      // An operator's @ internal note is a trigger, not a customer-visible reply.
      if (ev.public === false || ev.metadata?.is_public === false || ev.author?.type === 'system') continue;
      const authorName = ev.author?.display_name || '';
      const authorType = ev.author?.type || '';
      const isAgent = authorType === 'agent' || authorName.includes('David Yang') || authorName.includes('AI Agent');
      const { text, attachments } = extractEventDetails(ev);

      if (!latestEventId) {
        latestEventId = String(ev.id || ev.reference || '');
        latestEventTime = String(ev.created_at || '');
      }

      if (isAgent) {
        // Encountered the most recent agent reply! Stop collecting customer messages before this reply.
        foundAgentReply = true;
        break;
      } else {
        // Customer message sent after the latest agent reply
        if (text || attachments.length > 0) {
          unrepliedCustomerMessages.unshift({
            id: String(ev.id || ev.reference || ''),
            text: text,
            time: String(ev.created_at || ''),
            attachments: attachments,
          });
        }
      }
    }

    // If there are no customer messages after the latest agent reply:
    if (unrepliedCustomerMessages.length === 0 && foundAgentReply) {
      isAlreadyAnswered = true;
    }

    // Combine all unreplied consecutive customer messages (separated by double newline)
    let customerMessage = '';
    if (unrepliedCustomerMessages.length > 0) {
      customerMessage = unrepliedCustomerMessages
        .map(m => {
          let part = m.text;
          if (m.attachments.length > 0) {
            const attList = m.attachments
              .map(a => `[Attachment: ${a.name} (${a.contentType || 'file'}), URL: ${a.url || 'N/A'}]`)
              .join('\n');
            part = part ? `${part}\n${attList}` : attList;
          }
          return part;
        })
        .filter(Boolean)
        .join('\n\n');
    }

    if (!customerMessage) {
      customerMessage = subject || `Ticket #${ticketId}`;
    }

    const lastEventId = unrepliedCustomerMessages.length > 0
      ? unrepliedCustomerMessages[unrepliedCustomerMessages.length - 1].id
      : latestEventId;
    const lastEventTime = unrepliedCustomerMessages.length > 0
      ? unrepliedCustomerMessages[unrepliedCustomerMessages.length - 1].time
      : latestEventTime;

    return {
      message: customerMessage,
      conversationContext: boundedHistory.join('\n\n'),
      email,
      attachments: [...new Map(allAttachments.map(attachment => [attachment.id, attachment])).values()].slice(-8),
      lastEventId,
      lastEventTime,
      customerEventsCount: unrepliedCustomerMessages.length,
      hasRequestedHuman,
      isAlreadyAnswered,
      unrepliedCount: unrepliedCustomerMessages.length,
    };
  } catch (err: any) {
    throw new Error(`Unable to load complete ticket context for #${ticketId}: ${err.message}`);
  }
}
