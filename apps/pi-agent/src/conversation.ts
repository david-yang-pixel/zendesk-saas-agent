import type { McpServiceConfig } from './mcp.js';
import { callOpenConnectorCustomMcpTool, parseOpenConnectorResult } from './openconnector.js';

export async function loadConversation(ticketId: number, config: McpServiceConfig, signal?: AbortSignal): Promise<any[]> {
  const events: any[] = [];
  const cursors = new Set<string>();
  let after: string | undefined;
  for (let page = 0; page < 100; page++) {
    const result = await callOpenConnectorCustomMcpTool(config, 'zendesk', 'get_ticket_conversation_log', {
      ticket_id: ticketId,
      force_refresh: true,
      conversation_page_size: 25,
      ...(after ? { conversation_after: after } : {}),
    }, signal);
    const payload = parseOpenConnectorResult(result);
    if (!Array.isArray(payload.data?.events)) {
      const stored = payload['io.oomol.open-connector/stored-result'];
      throw new Error(stored ? 'Zendesk conversation exceeded the OpenConnector inline limit' : 'OpenConnector conversation response is missing data.events');
    }
    events.push(...payload.data.events);
    const meta = payload.data.meta;
    if (typeof meta?.has_more !== 'boolean') throw new Error('Zendesk conversation is missing pagination metadata');
    if (!meta.has_more) return events;
    after = meta.after_cursor;
    if (typeof after !== 'string' || !after || cursors.has(after)) {
      throw new Error('Zendesk conversation has a missing or repeated cursor; refusing incomplete context');
    }
    cursors.add(after);
  }
  throw new Error('Zendesk conversation exceeded 100 pages; refusing incomplete context');
}
