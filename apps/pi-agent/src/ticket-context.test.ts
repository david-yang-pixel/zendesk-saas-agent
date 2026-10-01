import { beforeEach, expect, it, vi } from 'vitest';
import { getTicketDetails } from './ticket-context.js';
import { loadConversation } from './conversation.js';
import { callOpenConnectorCustomMcpTool } from './openconnector.js';
vi.mock('./conversation.js', () => ({ loadConversation: vi.fn() }));
vi.mock('./openconnector.js', async original => ({
  ...await original<object>(), callOpenConnectorCustomMcpTool: vi.fn(),
}));
const config = { name: 'test', endpoint: 'https://example.test', apiKey: 'test' };
beforeEach(() => {
  vi.mocked(callOpenConnectorCustomMcpTool).mockResolvedValue({ structuredContent: { data: { ticket: { subject: 'Old subject' } } } });
});
it('does not treat the triggering internal mention as an answer', async () => {
  vi.mocked(loadConversation).mockResolvedValue([
    { id: '1', author: { type: 'agent' }, content: { text: 'Previous public answer' } },
    { id: '2', author: { type: 'end-user' }, content: { text: '你好' } },
    { id: '3', author: { type: 'agent' }, content: { text: '@David Yang' }, metadata: { is_public: false } },
  ]);
  const result = await getTicketDetails(3726, config);
  expect(result.message).toBe('你好');
  expect(result.isAlreadyAnswered).toBe(false);
  expect(result.lastEventId).toBe('2');
});
it('still recognizes a public agent reply', async () => {
  vi.mocked(loadConversation).mockResolvedValue([
    { id: '1', author: { type: 'end-user' }, content: { text: '你好' } },
    { id: '2', author: { type: 'agent' }, content: { text: '您好' } },
  ]);
  expect((await getTicketDetails(3726, config)).isAlreadyAnswered).toBe(true);
});
it('rejects failed context reads rather than analyzing the ticket title', async () => {
  vi.mocked(loadConversation).mockRejectedValue(new Error('upstream unavailable'));
  await expect(getTicketDetails(3726, config)).rejects.toThrow('Unable to load complete ticket context');
});

it('preserves earlier answers, requester identity and attachments without exposing internal notes or bot messages', async () => {
  vi.mocked(loadConversation).mockResolvedValue([
    { id: 'form', author: { type: 'end-user' }, content: { text_fallback: 'Workspace: example', fields: [{ type: 'EMAIL', email: 'customer@example.test' }] } },
    { id: 'receipt', author: { type: 'end-user' }, content: { type: 'file', media_url: 'https://example.zendesk.com/receipt.pdf', media_type: 'application/pdf', attachment_id: 'pdf', alt_text: 'receipt.pdf' } },
    { id: 'answer', author: { type: 'agent' }, content: { text: 'Earlier public support answer' } },
    { id: 'private', author: { type: 'agent' }, content: { text: 'private analysis' }, metadata: { is_public: false } },
    { id: 'new', author: { type: 'end-user' }, content: { text: 'Please check the receipt I already sent.' } },
    { id: 'bot', author: { type: 'system' }, content: { text: 'Automatic greeting' } },
  ]);
  const result = await getTicketDetails(999999, config);
  expect(result.message).toBe('Please check the receipt I already sent.');
  expect(result.email).toBe('customer@example.test');
  expect(result.conversationContext).toContain('Earlier public support answer');
  expect(result.conversationContext).toContain('receipt.pdf');
  expect(result.conversationContext).not.toMatch(/private analysis|Automatic greeting/);
  expect(result.attachments).toEqual([expect.objectContaining({ id: 'pdf', contentType: 'application/pdf' })]);
  expect(result.lastEventId).toBe('new');
});
