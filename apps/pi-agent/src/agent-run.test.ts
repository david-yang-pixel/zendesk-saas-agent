import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CustomTool } from './mcp.js';

const mocked = vi.hoisted(() => ({
  messages: [] as any[], prompts: [] as string[], tools: [] as any[],
  subscriber: undefined as ((event: any) => void) | undefined,
  initial: undefined as (() => Promise<void>) | undefined,
  recovery: undefined as (() => Promise<void>) | undefined,
  review: undefined as (() => Promise<void>) | undefined,
  reviewText: undefined as string | undefined,
  reviewDecision: undefined as any,
  reviewStopReason: undefined as string | undefined,
  splitReviewText: false,
  fileHook: undefined as ((event: any) => any) | undefined,
  dispose: vi.fn(),
  results: new Map<string, any>(),
}));
vi.mock('@earendil-works/pi-coding-agent', () => ({
  ModelRuntime: { create: async () => ({ registerProvider() {}, getModel: () => ({}) }) },
  createAgentSession: async (options: any) => {
    mocked.tools = options.customTools;
    return { session: {
      get messages() { return mocked.messages; },
      subscribe(callback: (event: any) => void) { mocked.subscriber = callback; },
      async prompt(prompt: string) {
        mocked.prompts.push(prompt);
        mocked.messages.push({ role: 'user', content: prompt });
        if (prompt.startsWith('Final customer reply quality review')) {
          await mocked.review?.();
          const text = mocked.reviewText ?? '=== INTERNAL NOTE ===\nReply reviewed.\n=== CUSTOMER REPLY ===\nHello, here is what we verified.';
          const reviewedText = text.replace(
            '=== INTERNAL NOTE ===\n', `=== INTERNAL NOTE ===\n${JSON.stringify(mocked.reviewDecision)}\n`,
          );
          mocked.messages.push({ role: 'assistant', stopReason: mocked.reviewStopReason,
            content: [{ type: 'text', text: reviewedText }, ...(mocked.splitReviewText ? [{ type: 'text', text: 'Additional reviewed next step.' }] : [])] });
          return;
        }
        if (mocked.prompts.length === 1) await mocked.initial?.();
        if (mocked.prompts.length === 2) await mocked.recovery?.();
        mocked.messages.push({ role: 'assistant', content: [{ type: 'text', text:
          '=== INTERNAL NOTE ===\nEvidence checked.\n=== CUSTOMER REPLY ===\nHello, here is what we verified.' }] });
      },
      dispose: mocked.dispose,
    } };
  },
  SessionManager: { inMemory: () => ({}) },
  DefaultResourceLoader: class {
    constructor(options: any) {
      for (const factory of options.extensionFactories ?? []) factory({ on(event: string, handler: any) {
        if (event === 'before_provider_request') mocked.fileHook = handler;
      } });
    }
    async reload() {}
  }, getAgentDir: () => '/tmp',
}));
vi.mock('./project-skills.js', () => ({
  getProjectSkills: () => [{ name: 'dify-saas-support', filePath: '/tmp/SKILL.md' }],
  createProjectSkillReadTool: () => ({ name: 'read', label: 'read', description: 'read', promptSnippet: 'read', parameters: {}, execute: async () => ({ content: [] }) }),
}));

import { runPiSupportAgent, type AgentTracePayload } from './agent.js';
import { buildInternalDraftBody } from './internal-draft-delivery.js';
const external: CustomTool = { name: 'execute_action', label: '', description: '', promptSnippet: '', parameters: {},
  execute: async id => mocked.results.get(id) ?? { ok: true, data: { content: 'Current dated policy body.' } } };
async function execute(name: string, id: string, args: any) {
  mocked.subscriber?.({ type: 'tool_execution_start', toolCallId: id, toolName: name, args });
  const result = await mocked.tools.find(tool => tool.name === name).execute(id, args);
  mocked.subscriber?.({ type: 'tool_execution_end', toolCallId: id, toolName: name, args, result, isError: result.isError });
  return result;
}
const update = (id: string, checks: any[]) => execute('investigation_check', id, { checks });
const check = (id: string, source: string, kind = 'document') => ({ id, question: 'What does the current policy grant?', source, kind, status: 'pending' });
const read = (id: string, source: string, tool: string) => execute('execute_action', id, source.startsWith('dify-')
  ? { connectionName: source, actionId: 'custom_mcp.call_tool', input: { toolName: tool, arguments: {} } }
  : { connectionName: 'default', actionId: source + '.' + tool, input: {} });
const options = { ticketId: 3726, customerMessage: 'Check the education offer and credit refresh.', tools: [external], apiKey: 'test-key', modelBaseUrl: 'https://example.test/v1', modelName: 'test-model', publicReplyAllowed: false };
beforeEach(() => {
  mocked.messages = []; mocked.prompts = []; mocked.tools = []; mocked.results.clear();
  mocked.subscriber = undefined; mocked.initial = undefined; mocked.recovery = undefined;
  mocked.review = undefined; mocked.reviewText = undefined; mocked.dispose.mockClear();
  mocked.reviewDecision = { approved: true, claimMode: 'qualified', answersLatestQuestion: true, requestsOnlyMissingDetails: true, usesApplicableEvidence: true };
  mocked.reviewStopReason = undefined; mocked.splitReviewText = false;
  mocked.fileHook = undefined;
});

describe('Pi Agent evidence continuation and delivery', () => {
  it('repairs an absent plan through registered tools and stops after a reviewed read', async () => {
    mocked.recovery = async () => {
      await update('plan', [check('policy', 'feishu')]);
      await read('body', 'feishu', 'get_document_content');
      await update('review', [{ id: 'policy', status: 'verified', callIds: ['body'], explanation: 'Read the current applicable dated policy.' }]);
    };
    let captured: AgentTracePayload | undefined;
    const result = await runPiSupportAgent({ ...options, onTrace: trace => { captured = trace; } });
    expect(mocked.prompts).toHaveLength(3);
    expect(mocked.prompts[1]).toContain('No evidence plan');
    expect(captured?.toolCalls.filter(call => call.toolName === 'execute_action')).toHaveLength(1);
    expect(result.internalNote).toContain('policy [feishu]: verified');
    expect(result.reply).toBe('');
  });
  it('continues an empty search and never-discovered official source despite completed DB reads', async () => {
    mocked.initial = async () => {
      await update('plan', [check('policy', 'feishu'), check('public', 'dify-docs'), check('account', 'dify-db', 'record')]);
      await read('db', 'dify-db', 'db_gateway_query');
      await update('account-review', [{ id: 'account', status: 'verified', callIds: ['db'], explanation: 'Matching account state checked.' }]);
      mocked.results.set('empty', { ok: true, data: { results: [], total: 0 } });
      await read('empty', 'feishu', 'search_documents');
    };
    mocked.recovery = async () => {
      await read('policy-body', 'feishu', 'get_document_content');
      await read('official-body', 'dify-docs', 'official_docs_read');
      await update('review', [
        { id: 'policy', status: 'verified', callIds: ['policy-body'], explanation: 'Applicable current policy.' },
        { id: 'public', status: 'verified', callIds: ['official-body'], explanation: 'Public guidance agrees for this plan.' },
      ]);
    };
    const result = await runPiSupportAgent(options);
    expect(mocked.prompts).toHaveLength(3);
    expect(mocked.prompts[1]).toContain('search_empty');
    expect(mocked.prompts[1]).toContain('public (dify-docs, document): not_queried');
    expect(result.internalNote).not.toContain('Remaining investigation gaps');
    expect(result.customerReply).toBe('Hello, here is what we verified.');
    const draftBody = buildInternalDraftBody('job-3726', result.customerReply);
    expect(draftBody).toContain('Not sent to the customer.');
    expect(draftBody).not.toContain('Evidence coverage:');
  });
  it('keeps actual failures in the trace and still returns a useful draft', async () => {
    mocked.reviewText = '=== INTERNAL NOTE ===\nBilling read failed; no subscription state verified.\n=== CUSTOMER REPLY ===\nI could not verify the billing records for this workspace. A billing engineer needs to check the subscription; no further customer details are missing.';
    mocked.initial = async () => {
      await update('plan', [check('account', 'dify-db', 'record')]);
      mocked.results.set('bad-sql', { isError: true, content: [{ type: 'text', text: 'Tool error: malformed SQL' }] });
      await read('bad-sql', 'dify-db', 'db_gateway_query');
      await update('review', [{ id: 'account', status: 'unresolved', callIds: ['bad-sql'], explanation: 'Query failed; account evidence remains unavailable.' }]);
    };
    const result = await runPiSupportAgent(options);
    expect(result.trace.toolCalls.find(call => call.toolCallId === 'bad-sql')).toMatchObject({ status: 'failed' });
    expect(result.customerReply).toContain('could not verify the billing records');
    expect(result.reply).toBe('');
    expect(result.internalNote).toContain('No connected business read succeeded');
  });
  it('reviews a qualified no-evidence reply without overwriting it with a template', async () => {
    mocked.reviewText = '=== INTERNAL NOTE ===\nNo business evidence.\n=== CUSTOMER REPLY ===\nPlease describe the error you are seeing so we can identify the affected operation.';
    const result = await runPiSupportAgent({ ...options, publicReplyAllowed: true });
    expect(mocked.prompts).toHaveLength(4);
    expect(result.customerReply).toContain('describe the error');
    expect(result.reply).toContain('This reply was sent by Dify AI Agent');
    expect(result.reply).not.toContain('here is what we verified');
  });
  it('answers a complete thank-you without initializing a model or investigating', async () => {
    const result = await runPiSupportAgent({ ...options, publicReplyAllowed: true,
      customerMessage: 'Perfect I should be able to work with that. Thank you' });
    expect(result.customerReply).toBe("You're welcome!");
    expect(result.trace.toolCalls).toEqual([]);
    expect(mocked.prompts).toEqual([]);
  });
  it('answers a Japanese language request directly in Japanese without tool calls', async () => {
    const result = await runPiSupportAgent({ ...options, publicReplyAllowed: true, customerMessage: '日本語で話せますか' });
    expect(result.customerReply).toContain('日本語');
    expect(result.reply).not.toContain('This reply was sent');
    expect(mocked.prompts).toEqual([]);
  });
  it('supplies earlier context and accepts the newly reviewed customer-language answer', async () => {
    mocked.reviewText = '=== INTERNAL NOTE ===\nNo account claim required.\n=== CUSTOMER REPLY ===\nエラーが発生する操作を教えてください。';
    const result = await runPiSupportAgent({ ...options, publicReplyAllowed: true, customerMessage: 'まだ動きません',
      conversationContext: 'Customer: I use the Dify Cloud website.\nSupport: Please try again.' });
    expect(mocked.prompts[0]).toContain('I use the Dify Cloud website');
    expect(mocked.prompts.at(-1)).toContain('Do not ask again for details or attachments already present');
    expect(result.customerReply).toContain('教えて');
    expect(mocked.dispose).toHaveBeenCalledOnce();
  });
  it('does not reuse the first draft if the quality review fails and disposes the session', async () => {
    mocked.review = async () => { throw new Error('review unavailable'); };
    let captured: AgentTracePayload | undefined;
    await expect(runPiSupportAgent({ ...options, onTrace: trace => { captured = trace; } })).rejects.toThrow('review unavailable');
    expect(captured?.finalReply).toBe('');
    expect(mocked.dispose).toHaveBeenCalledOnce();
  });
  it('blocks an empty review instead of delivering the earlier draft', async () => {
    mocked.reviewText = '';
    await expect(runPiSupportAgent(options)).rejects.toThrow('no text response');
    expect(mocked.dispose).toHaveBeenCalledOnce();
  });
  it('blocks a supposedly verified reply when no business read succeeded', async () => {
    mocked.reviewDecision.claimMode = 'verified';
    await expect(runPiSupportAgent(options)).rejects.toThrow('verified reply has no successful business evidence');
  });
  it('blocks a review that still asks for already supplied details', async () => {
    mocked.reviewDecision.requestsOnlyMissingDetails = false;
    await expect(runPiSupportAgent(options)).rejects.toThrow('reply quality review did not pass');
  });
  it('accepts a fresh reviewed answer after SDK history compaction', async () => {
    mocked.review = async () => { mocked.messages = [{ role: 'user', content: 'Compacted history and the current review instruction' }]; };
    expect((await runPiSupportAgent(options)).customerReply).toBe('Hello, here is what we verified.');
  });
  it('blocks a truncated review and never sends its partial text', async () => {
    mocked.reviewStopReason = 'length';
    await expect(runPiSupportAgent(options)).rejects.toThrow('no text response');
    expect(mocked.dispose).toHaveBeenCalledOnce();
  });
  it('preserves all reviewed text blocks in the final answer', async () => {
    mocked.splitReviewText = true;
    expect((await runPiSupportAgent(options)).customerReply).toContain('Additional reviewed next step.');
  });
  it('does not deliver the first draft when the review is cancelled', async () => {
    const controller = new AbortController();
    mocked.review = async () => { controller.abort(); };
    await expect(runPiSupportAgent({ ...options, abortSignal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
    expect(mocked.dispose).toHaveBeenCalledOnce();
  });
  it('blocks delivery and keeps a failed trace when the native file hook cannot build a Responses request', async () => {
    const fileData = 'data:application/pdf;base64,JVBERi0xLjQ=';
    mocked.initial = async () => {
      mocked.results.set('receipt', { content: [{ type: 'text', text: 'Receipt downloaded' }],
        modelFiles: [{ type: 'input_file', filename: 'receipt.pdf', file_data: fileData }] });
      await read('receipt', 'zendesk', 'download_attachment');
      try { mocked.fileHook?.({ payload: { messages: [] } }); } catch { /* SDK reports extension errors and continues. */ }
    };
    let captured: AgentTracePayload | undefined;
    await expect(runPiSupportAgent({ ...options, onTrace: trace => { captured = trace; } })).rejects.toThrow('Native attachment transport failed');
    expect(captured?.finalReply).toBe('');
    expect(captured?.toolCalls).toHaveLength(1);
    expect(JSON.stringify(captured)).not.toContain(fileData);
    expect(mocked.dispose).toHaveBeenCalledOnce();
    // Final cleanup releases the run's binary sidecar even after an extension failure.
    expect(mocked.fileHook?.({ payload: { input: [] } })).toEqual({ input: [] });
  });
});
