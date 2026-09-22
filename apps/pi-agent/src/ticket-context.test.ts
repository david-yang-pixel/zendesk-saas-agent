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
