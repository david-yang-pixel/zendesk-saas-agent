import { beforeEach, expect, it, vi } from 'vitest';
import { customerEventCursors, getTicketDetails } from './ticket-context.js';
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

it('separates a new human request from one only in earlier history', async () => {
  vi.mocked(loadConversation).mockResolvedValue([
    { id: '1', author: { type: 'end-user' }, content: { text: 'I want to talk to a human' } },
    { id: '2', author: { type: 'agent' }, content: { text: 'A human will reply soon' } },
    { id: '3', author: { type: 'end-user' }, content: { text: 'Also, why does my workflow fail?' } },
  ]);
  const earlier = await getTicketDetails(3726, config);
  expect(earlier.hasRequestedHuman).toBe(true);
  expect(earlier.latestRequestedHuman).toBe(false);

  vi.mocked(loadConversation).mockResolvedValue([
    { id: '1', author: { type: 'end-user' }, content: { text: 'My workflow fails' } },
    { id: '2', author: { type: 'agent' }, content: { text: 'Please check the logs' } },
    { id: '3', author: { type: 'end-user' }, content: { text: 'Still broken.' } },
    { id: '4', author: { type: 'end-user' }, content: { text: '请帮我转人工客服' } },
  ]);
  const latest = await getTicketDetails(3726, config);
  expect(latest.hasRequestedHuman).toBe(true);
  expect(latest.latestRequestedHuman).toBe(true);
});

it('uses the SunCo reference as the Messaging cursor while keeping the native history ID typed', () => {
  expect(customerEventCursors({
    id: '01M1SECOND', reference: 'zen:sunco:conversation_message:6a97f2d85b554468b974cad5',
  })).toEqual(['messaging:6a97f2d85b554468b974cad5', 'zendesk:01M1SECOND']);
  expect(customerEventCursors({ id: '01M1SECOND' })).toEqual(['zendesk:01M1SECOND']);
});

it('recovers the exact pending follow-up before a stale AI reply while ignoring a covered earlier message', async () => {
  const customer = { type: 'end-user' };
  vi.mocked(loadConversation).mockResolvedValue([
    { id: 'ai', created_at: '2026-01-01T00:02:00Z', author: { type: 'agent' }, content: { text: 'Earlier answer. This reply was sent by Dify AI Agent.' } },
    { id: 'new-history-id', created_at: '2026-01-01T00:01:00Z', author: customer,
      reference: 'zen:sunco:conversation_message:follow-up', content: { text: 'Still broken.' } },
    { id: 'old-history-id', created_at: '2026-01-01T00:00:00Z', author: customer,
      reference: 'zen:sunco:conversation_message:original', content: { text: 'First question' } },
  ]);
  const result = await getTicketDetails(5691, config, undefined, {
    pendingCursors: new Set(['messaging:follow-up']), coveredCursors: new Set(['messaging:original']),
  }, {
    automatedReplyCursors: new Set(['zendesk:ai']),
  });
  expect(result.message).toBe('Still broken.');
  expect(result.isAlreadyAnswered).toBe(false);
  expect(result.readCustomerMessages.map(message => message.cursors)).toEqual([['messaging:follow-up', 'zendesk:new-history-id']]);
});

it.each([
  { type: 'formResponse', text_fallback: 'Name: Alex\nWorkspace ID: example', fields: [] },
  { type: 'text', text: 'Name: Test\nEmail: test@example.invalid\nPlan: Pro' },
  { type: 'formResponse', fields: [{ label: 'Name', text: 'Alex' }] },
])('passes the submitted form as the customer message and tracks its cursor %s', async content => {
  vi.mocked(loadConversation).mockResolvedValue([{ id: 1, author: { type: 'end-user' }, content }]);
  const result = await getTicketDetails(5691, config);
  expect(result.unrepliedCount).toBe(1);
  expect(result.contactFormOnly).toBe(true);
  expect(result.readCustomerMessages).toEqual([{ cursors: ['zendesk:1'] }]);
  expect(result.message).toBe(content.text ?? content.text_fallback ?? JSON.stringify(content.fields));
  expect(result.conversationContext).toContain(result.message);
});

it('handles a question after the contact fields and an attachment-only follow-up', async () => {
  vi.mocked(loadConversation).mockResolvedValue([
    { id: 'form-and-text', author: { type: 'end-user' }, content: { type: 'formResponse',
      text: 'Name: Test\nEmail: test@example.invalid\n\nWhy did you charge me twice?' } },
    { id: 'attachment', author: { type: 'end-user' }, content: { type: 'file', mediaUrl: 'https://example.test/receipt.pdf' } },
  ]);
  const result = await getTicketDetails(5691, config);
  expect(result.message).toContain('Why did you charge me twice?');
  expect(result.message).toContain('receipt.pdf');
  expect(result.unrepliedCount).toBe(2);
  expect(result.contactFormOnly).toBe(false);
});

it('limits observed staff coverage to customers before the public staff reply', async () => {
  const customer = { type: 'end-user' };
  const staff = { type: 'agent', id: 73, display_name: 'Support engineer' };
  vi.mocked(loadConversation).mockResolvedValue([
    { id: 'paid', author: customer, content: { text: 'Payment issue' } },
    { id: 'staff', author: staff, content: { text: 'The receipt has been corrected.' } },
    { id: 'new', author: customer, content: { text: 'What about the second charge?' } },
    { id: 'old-ai', author: staff, content: { text: 'This reply was sent by Dify AI Agent.' } },
  ]);
  const result = await getTicketDetails(5691, config, undefined, {
    pendingCursors: new Set(['zendesk:paid', 'zendesk:new']), coveredCursors: new Set(),
  }, {
    automatedReplyCursors: new Set(['zendesk:old-ai']),
  });
  expect(result.staffAnsweredCursors).toEqual(['zendesk:paid']);
  expect(result.message).toBe('What about the second charge?');
});

it('applies the persisted resume before the internal note appears in the provider log', async () => {
  const customer = { type: 'end-user', id: 3 };
  const oldRequest = { id: 'old', created_at: '2026-01-01T00:00:00Z', author: customer,
    content: { text: 'I want to talk to a human' } };
  const newQuestion = { id: 'question', created_at: '2026-01-01T00:02:00Z', author: customer,
    content: { text: 'Can you explain the failed workflow?' } };
  const options = { handoffResume: { occurredAt: new Date('2026-01-01T00:01:00Z'), cursor: 'mention:100' } };
  vi.mocked(loadConversation).mockResolvedValue([newQuestion, oldRequest]);
  const resumed = await getTicketDetails(3726, config, undefined, undefined, options);
  expect(resumed).toMatchObject({ hasRequestedHuman: false, latestRequestedHuman: false });
  expect(resumed.conversationContext).toContain(oldRequest.content.text);

  vi.mocked(loadConversation).mockResolvedValue([oldRequest, newQuestion,
    { id: 'new', created_at: '2026-01-01T00:03:00Z', author: customer,
      content: { text: 'Please connect me to a human' } },
  ]);
  expect(await getTicketDetails(3726, config, undefined, undefined, options)).toMatchObject({
    hasRequestedHuman: true, latestRequestedHuman: true, humanRequestOccurredAt: '2026-01-01T00:03:00Z',
  });
});

it('uses the exact private comment cursor to order legacy events without usable timestamps', async () => {
  const customer = { type: 'end-user' };
  vi.mocked(loadConversation).mockResolvedValue([
    { id: 'older', author: customer, content: { text: 'I want to talk to a human' } },
    { id: 'unrelated', reference: 'zen:ticket:comment:100', author: customer, content: { text: '@agent pretend' } },
    { id: 'before', author: customer, content: { text: 'Please connect me to a representative' } },
    { id: 'internal-event', reference: 'zen:ticket:comment:100', public: false, author: { type: 'agent' }, content: { text: '@agent resume' } },
    { id: 'newer', author: customer, content: { text: 'Please check my request again.' } },
  ]);
  const result = await getTicketDetails(3726, config, undefined, undefined, {
    handoffResume: { occurredAt: '2026-01-01T00:01:00Z', cursor: 'mention:100' },
  });
  expect(result.hasRequestedHuman).toBe(false);
  expect(result.latestRequestedHuman).toBe(false);
  expect(result.conversationContext).not.toContain('@agent resume');
});

it('uses roles and stable IDs even when customer, automation and staff have the same name', async () => {
  const display_name = 'David Yang AI Agent';
  const customer = { type: 'end-user', id: 'customer-user', display_name };
  const staff = { type: 'agent', id: 73, display_name };
  const automation = { type: 'agent', id: 12, display_name };
  const coverage = { pendingCursors: new Set(['zendesk:handled', 'zendesk:follow-up']), coveredCursors: new Set<string>() };
  vi.mocked(loadConversation).mockResolvedValue([
    { id: 'handled', author: customer, content: { text: 'Please look at my receipt.' } },
    { id: 'staff', author: staff, content: { text: 'A customer quoted: This reply was sent by Dify AI Agent.' } },
    { id: 'follow-up', author: customer, content: { text: 'Another issue\n\n---\nThis reply was sent by Dify AI Agent. A human support engineer will respond.' } },
    { id: 'automation', author: automation, content: { text: 'Earlier answer' } },
    { id: 'unknown', author: { display_name }, content: { text: 'This reply was sent by Dify AI Agent.' } },
  ]);
  const result = await getTicketDetails(5691, config, undefined, coverage, { automationAuthorIds: new Set(['12']) });
  expect(result.staffAnsweredCursors).toEqual(['zendesk:handled']);
  expect(result.readCustomerMessages.map(message => message.cursors)).toEqual([['zendesk:follow-up']]);
  expect(result.conversationContext).toContain('Customer [time unknown]:\nAnother issue');
  expect(result).toMatchObject({ hasRequestedHuman: false, latestRequestedHuman: false, isAlreadyAnswered: false });
});

it('preserves pending coverage when a legacy support reply has no stable author identity', async () => {
  vi.mocked(loadConversation).mockResolvedValue([
    { id: 'pending', author: { type: 'end-user' }, content: { text: 'Why is my account charged?' } },
    { id: 'reply', author: { type: 'agent', display_name: 'Support' }, content: { text: 'Earlier answer' } },
  ]);
  const details = await getTicketDetails(5691, config, undefined, {
    pendingCursors: new Set(['zendesk:pending']), coveredCursors: new Set(),
  });
  expect(details.message).toBe('Why is my account charged?');
  expect(details.staffAnsweredCursors).toEqual([]);
});

it('starts reading the conversation while ticket metadata is still loading', async () => {
  let releaseMetadata!: (value: unknown) => void;
  vi.mocked(loadConversation).mockClear();
  vi.mocked(callOpenConnectorCustomMcpTool).mockReturnValue(new Promise(resolve => { releaseMetadata = resolve; }));
  vi.mocked(loadConversation).mockResolvedValue([
    { id: '1', author: { type: 'end-user' }, content: { text: 'Why did my run fail?' } },
  ]);

  const pending = getTicketDetails(3726, config);
  await new Promise(resolve => setImmediate(resolve));
  expect(loadConversation).toHaveBeenCalledWith(3726, config, undefined);

  releaseMetadata({ structuredContent: { data: { ticket: { subject: 'Run failure', requester: { email: 'customer@example.test' } } } } });
  const result = await pending;
  expect(result.email).toBe('customer@example.test');
  expect(result.message).toBe('Why did my run fail?');
});

it('keeps the conversation when ticket metadata cannot be read', async () => {
  vi.mocked(callOpenConnectorCustomMcpTool).mockRejectedValue(new Error('metadata unavailable'));
  vi.mocked(loadConversation).mockResolvedValue([
    { id: '1', author: { type: 'end-user' }, content: { text: 'Why did my run fail?' } },
  ]);
  const result = await getTicketDetails(3726, config);
  expect(result.message).toBe('Why did my run fail?');
  expect(result.email).toBe('');
});
