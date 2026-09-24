import { describe, expect, it, vi } from 'vitest';
import { InvestigationTracker, continueInvestigation } from './investigation.js';

const policy = { id: 'policy', question: 'Does the current education offer include credits?', source: 'feishu', kind: 'document' as const, status: 'pending' as const };
const docs = { ...policy, id: 'public', source: 'dify-docs' };
function call(id: string, source: string, action: string, input: any, data: any) {
  return { toolCallId: id, toolName: 'execute_action', status: 'succeeded', arguments: source.startsWith('dify-')
    ? { actionId: 'custom_mcp.call_tool', connectionName: source, input: { toolName: action, arguments: input } }
    : { actionId: `${source}.${action}`, connectionName: 'default', input }, result: { content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] } };
}

describe('claim-oriented investigation', () => {
  it('normalizes explicit provider/connection source aliases before matching real reads', () => {
    const tracker = new InvestigationTracker();
    tracker.update([{ ...policy, source: 'feishu/default' }, { ...docs, source: 'custom_mcp/dify-docs' }]);
    tracker.observe(call('feishu-body', 'feishu', 'fetch_document', {}, { content: 'Current policy.' }));
    tracker.observe(call('docs-body', 'dify-docs', 'official_docs_read', {}, { content: 'Current product guidance.' }));
    tracker.update([
      { id: 'policy', status: 'verified', callIds: ['feishu-body'], explanation: 'Reviewed policy body.' },
      { id: 'public', status: 'verified', callIds: ['docs-body'], explanation: 'Reviewed guidance body.' },
    ]);
    expect(tracker.snapshot().map(check => check.source)).toEqual(['feishu', 'dify-docs']);
    expect(tracker.gaps()).toEqual([]);
    tracker.update([{ ...policy, id: 'other', source: 'feishu/secondary' }]);
    expect(tracker.snapshot().at(-1)?.phase).toBe('not_queried');
  });

  it('keeps an empty policy search and an undiscovered official source open despite successful DB reads', () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy, docs]);
    tracker.observe(call('db', 'dify-db', 'db_gateway_query', { sql: 'SELECT plan FROM subscriptions' }, { rows: [{ plan: 'professional' }] }));
    tracker.observe(call('empty', 'feishu', 'search_documents', { query: '教育版 积分 刷新' }, { results: [], total: 0 }));
    expect(tracker.snapshot().map(x => x.phase)).toEqual(['search_empty', 'not_queried']);
    expect(tracker.gaps()).toHaveLength(2);
  });

  it('requires a real document read and review, not a successful search or invented call ID', () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy]);
    tracker.observe(call('search', 'feishu', 'search_documents', { query: '教育' }, { results: [{ title: 'Education policy', token: 'doc-1' }] }));
    tracker.update([{ ...policy, status: 'verified', callIds: ['search'], explanation: 'A relevant title was found.' }]);
    expect(tracker.snapshot()[0].phase).toBe('candidate_unread');
    tracker.update([{ ...policy, status: 'verified', callIds: ['invented'], explanation: 'Policy says so.' }]);
    expect(tracker.gaps().join(' ')).toContain('call');
    expect(tracker.snapshot()[0].next).toContain('search (search_documents, succeeded, search/record)');
    tracker.observe(call('document', 'feishu', 'get_document_content', { documentId: 'doc-1' }, { content: 'Current education policy, effective September: discount only.' }));
    tracker.update([{ ...policy, status: 'verified', callIds: ['document'], explanation: 'Read the dated policy; it applies to the current education offer, not a legacy grant.' }]);
    expect(tracker.gaps()).toEqual([]);
  });

  it('does not let a read from another source verify the claim', () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy]);
    tracker.observe(call('db', 'dify-db', 'db_gateway_query', {}, { plan: 'professional' }));
    tracker.update([{ ...policy, status: 'verified', callIds: ['db'], explanation: 'Plan exists.' }]);
    expect(tracker.gaps()).toHaveLength(1);
  });

  it('allows an explicit unresolved result after distinct searches, but not identical retries', () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy]);
    for (const id of ['a', 'b']) tracker.observe(call(id, 'feishu', 'search_documents', { query: 'education credits' }, { results: [] }));
    tracker.update([{ ...policy, status: 'unresolved', callIds: ['a', 'b'], explanation: 'No matching policy found.' }]);
    expect(tracker.gaps()).toHaveLength(1);
    tracker.observe(call('c', 'feishu', 'search_documents', { query: 'student discount' }, { results: [] }));
    tracker.update([{ ...policy, status: 'unresolved', callIds: ['a', 'c'], explanation: 'Both targeted searches were empty; support must verify the current policy.' }]);
    expect(tracker.snapshot()[0].phase).toBe('unresolved');
    expect(tracker.gaps()).toEqual([]);
  });

  it('does not mistake nested provider failures for evidence and permits an honest access gap', () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy]);
    const failed = call('denied', 'feishu', 'get_document_content', {}, {});
    failed.result.content[0].text = JSON.stringify({ content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { message: 'Permission denied' } }) }] });
    tracker.observe(failed);
    tracker.update([{ ...policy, status: 'verified', callIds: ['denied'], explanation: 'Confirmed.' }]);
    expect(tracker.gaps()).toHaveLength(1);
    tracker.update([{ ...policy, status: 'unresolved', callIds: ['denied'], explanation: 'The policy read was denied; a policy owner needs to verify it.' }]);
    expect(tracker.gaps()).toEqual([]);
  });

  it('keeps independent questions on the same source separate', () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy, { ...policy, id: 'bug', question: 'Has the reported bug shipped?' }]);
    tracker.observe(call('document', 'feishu', 'get_document_content', {}, { content: 'Education policy body' }));
    tracker.update([{ ...policy, status: 'verified', callIds: ['document'], explanation: 'Current policy verified.' }]);
    expect(tracker.snapshot().map(x => x.phase)).toEqual(['verified', 'unreviewed']);
  });

  it('requires correction after one invalid-input failure instead of treating it as unavailable', () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy]);
    const failed = call('invalid', 'feishu', 'get_wiki_node', { token: 'wrong-kind' }, {});
    failed.result.content[0].text = JSON.stringify({ ok: false, error: { message: 'Invalid parameter: token type' } });
    tracker.observe(failed);
    tracker.update([{ ...policy, status: 'unresolved', callIds: ['invalid'], explanation: 'Could not read the policy.' }]);
    expect(tracker.snapshot()[0].phase).toBe('failed');
    expect(tracker.gaps()).toHaveLength(1);
  });

  it('exposes stable observation IDs through the real tool wrapper while retaining payload and errors', async () => {
    const tracker = new InvestigationTracker();
    const original = call('unused', 'feishu', 'search_documents', {}, { results: [], total: 0 });
    const tool = tracker.wrap({ name: 'execute_action', label: '', description: '', promptSnippet: '', parameters: {}, execute: vi.fn().mockResolvedValue(original.result) });
    const result = await tool.execute('real-id', original.arguments);
    expect(JSON.parse(result.content.at(-1).text)).toMatchObject({ observationId: 'real-id', source: 'feishu', empty: true });
    expect(result.content.slice(0, -1)).toEqual(original.result.content);
    expect(result.details).toEqual(original.result);
  });

  it('preserves pagination sidecars and resource links returned to the model', async () => {
    const tracker = new InvestigationTracker();
    const raw = { structuredContent: { 'io.oomol.open-connector/pagination': { nextCursor: 'ocmcp1.next' } },
      content: [{ type: 'resource_link', uri: 'mcp://result/page' }, { type: 'text', text: '{"records":[1]}' }] };
    const wrapped = tracker.wrap({ name: 'execute_action', label: '', description: '', promptSnippet: '', parameters: {}, execute: async () => raw });
    const result = await wrapped.execute('page', { actionId: 'custom_mcp.call_tool', connectionName: 'zendesk', input: { toolName: 'get_ticket_conversation_log' } });
    expect(result.structuredContent).toEqual(raw.structuredContent);
    expect(result.content.slice(0, -1)).toEqual(raw.content);
  });

  it('accepts reviewed thread message bodies returned by a list action', () => {
    const tracker = new InvestigationTracker();
    tracker.update([{ ...policy, kind: 'discussion' }]);
    tracker.observe(call('thread', 'feishu', 'list_thread_messages', {}, { messages: [{ body: 'Dated engineering discussion' }] }));
    tracker.update([{ ...policy, kind: 'discussion', status: 'verified', callIds: ['thread'], explanation: 'Read original thread and subsequent correction.' }]);
    expect(tracker.gaps()).toEqual([]);
  });

  it('continues to search and read planned missing sources, then stops without a redundant follow-up', async () => {
    const tracker = new InvestigationTracker();
    tracker.update([policy, docs]);
    tracker.observe(call('empty', 'feishu', 'search_documents', { query: 'education credits' }, { results: [] }));
    const prompt = vi.fn(async () => {
      for (const check of [policy, docs]) {
        const id = `read-${check.id}`;
        tracker.observe(call(id, check.source, check.source === 'feishu' ? 'get_document_content' : 'official_docs_read', {}, { content: 'Relevant dated policy text.' }));
        tracker.update([{ ...check, status: 'verified', callIds: [id], explanation: 'Reviewed the current applicable page.' }]);
      }
    });
    await continueInvestigation(tracker, prompt);
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(prompt.mock.calls[0]?.[0]).toContain('public');
    expect(tracker.gaps()).toEqual([]);
  });

  it('bounds no-progress follow-ups and preserves a reportable gap', async () => {
    const tracker = new InvestigationTracker();
    const prompt = vi.fn(async () => {});
    await continueInvestigation(tracker, prompt);
    expect(prompt).toHaveBeenCalledTimes(2);
    expect(tracker.gaps()).toHaveLength(1);
  });

  it('does not continue after cancellation or a model error', async () => {
    const tracker = new InvestigationTracker();
    const prompt = vi.fn();
    await continueInvestigation(tracker, prompt, { signal: AbortSignal.abort() });
    await continueInvestigation(tracker, prompt, { hasTerminalError: () => true });
    expect(prompt).not.toHaveBeenCalled();
  });
});
