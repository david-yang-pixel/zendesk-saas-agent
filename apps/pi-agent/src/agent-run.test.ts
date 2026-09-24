import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CustomTool } from './mcp.js';

const mocked = vi.hoisted(() => ({
  messages: [] as any[], prompts: [] as string[], tools: [] as any[],
  subscriber: undefined as ((event: any) => void) | undefined,
  initial: undefined as (() => Promise<void>) | undefined,
  recovery: undefined as (() => Promise<void>) | undefined,
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
        if (mocked.prompts.length === 1) await mocked.initial?.();
        if (mocked.prompts.length === 2) await mocked.recovery?.();
        mocked.messages.push({ role: 'assistant', content: [{ type: 'text', text:
          '=== INTERNAL NOTE ===\nEvidence checked.\n=== CUSTOMER REPLY ===\nHello, here is what we verified.' }] });
      },
    } };
  },
  SessionManager: { inMemory: () => ({}) },
  DefaultResourceLoader: class { async reload() {} }, getAgentDir: () => '/tmp',
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
const options = { ticketId: 3726, customerMessage: 'Check the education offer and credit refresh.', tools: [external], apiKey: 'test-key', modelBaseUrl: 'https://example.test/v1', publicReplyAllowed: false };
beforeEach(() => {
  mocked.messages = []; mocked.prompts = []; mocked.tools = []; mocked.results.clear();
  mocked.subscriber = undefined; mocked.initial = undefined; mocked.recovery = undefined;
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
    expect(mocked.prompts).toHaveLength(2);
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
    expect(mocked.prompts).toHaveLength(2);
    expect(mocked.prompts[1]).toContain('search_empty');
    expect(mocked.prompts[1]).toContain('public (dify-docs, document): not_queried');
    expect(result.internalNote).not.toContain('Remaining investigation gaps');
    expect(result.customerReply).toBe('Hello, here is what we verified.');
    expect(buildInternalDraftBody('job-3726', result.customerReply, result.internalNote)).toContain('Not sent to the customer.');
  });
  it('keeps actual failures in the trace and still returns a useful draft', async () => {
    mocked.initial = async () => {
      await update('plan', [check('account', 'dify-db', 'record')]);
      mocked.results.set('bad-sql', { isError: true, content: [{ type: 'text', text: 'Tool error: malformed SQL' }] });
      await read('bad-sql', 'dify-db', 'db_gateway_query');
      await update('review', [{ id: 'account', status: 'unresolved', callIds: ['bad-sql'], explanation: 'Query failed; account evidence remains unavailable.' }]);
    };
    const result = await runPiSupportAgent(options);
    expect(result.trace.toolCalls.find(call => call.toolCallId === 'bad-sql')).toMatchObject({ status: 'failed' });
    expect(result.customerReply).toContain('cannot verify the relevant records');
    expect(result.reply).toBe('');
    expect(result.internalNote).toContain('No connected business read succeeded');
  });
  it('bounds a noncompliant model and preserves a public uncertainty response', async () => {
    const result = await runPiSupportAgent({ ...options, publicReplyAllowed: true });
    expect(mocked.prompts).toHaveLength(3);
    expect(result.customerReply).toContain('cannot verify the relevant records');
    expect(result.reply).toContain('This reply was sent by Dify AI Agent');
    expect(result.reply).not.toContain('here is what we verified');
  });
});
