import { describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({
  messages: [] as any[],
  prompts: [] as string[],
  subscriber: undefined as ((event: any) => void) | undefined,
  executeRecovery: undefined as (() => void) | undefined,
}));

vi.mock('@earendil-works/pi-coding-agent', () => ({
  ModelRuntime: { create: async () => ({ registerProvider() {}, getModel: () => ({}) }) },
  createAgentSession: async () => ({ session: {
    get messages() { return mocked.messages; },
    subscribe(callback: (event: any) => void) { mocked.subscriber = callback; },
    async prompt(prompt: string) {
      mocked.prompts.push(prompt);
      if (mocked.prompts.length === 2) mocked.executeRecovery?.();
      mocked.messages.push({ role: 'assistant', content: [{ type: 'text', text:
        '=== INTERNAL NOTE ===\nEvidence checked.\n=== CUSTOMER REPLY ===\nHello, here is what we verified.' }] });
    },
  } }),
  SessionManager: { inMemory: () => ({}) },
  DefaultResourceLoader: class { async reload() {} },
  getAgentDir: () => '/tmp',
}));

vi.mock('./project-skills.js', () => ({
  getProjectSkills: () => [{ name: 'dify-saas-support', filePath: '/tmp/SKILL.md' }],
  createProjectSkillReadTool: () => ({ name: 'read', label: 'read', description: 'read', promptSnippet: 'read', parameters: {}, execute: async () => ({ content: [] }) }),
}));

import { runPiSupportAgent, type AgentTracePayload } from './agent.js';
import { buildInternalDraftBody } from './internal-draft-delivery.js';

function toolEvidence(connectionName: string, actionId: string, callId: string) {
  const args = { actionId, connectionName, input: {} };
  mocked.subscriber?.({ type: 'tool_execution_start', toolCallId: callId, toolName: 'execute_action', args });
  mocked.subscriber?.({ type: 'tool_execution_end', toolCallId: callId, toolName: 'execute_action', args,
    result: { ok: true, data: {} }, isError: false });
}

describe('Pi Agent recovery and failed-run trace', () => {
  it('uses one recovery prompt for omitted policy sources and preserves the full trace before delivery', async () => {
    mocked.messages = []; mocked.prompts = []; mocked.subscriber = undefined;
    mocked.executeRecovery = () => {
      toolEvidence('dify-db', 'custom_mcp.call_tool', 'db');
      toolEvidence('default', 'feishu.search_documents', 'feishu');
      toolEvidence('dify-docs', 'custom_mcp.call_tool', 'docs');
    };
    let captured: AgentTracePayload | undefined;
    const result = await runPiSupportAgent({
      ticketId: 3726, customerMessage: '看下这个工单用户的教育优惠会员，以及为什么积分不刷新',
      tools: [], apiKey: 'test-key', modelBaseUrl: 'https://example.test/v1',
      publicReplyAllowed: false, onTrace: trace => { captured = trace; },
    });
    expect(mocked.prompts).toHaveLength(2);
    expect(mocked.prompts[0]).toContain('feishu, docs, db-gateway');
    expect(mocked.prompts[1]).toContain('feishu, docs, db-gateway');
    expect(result.trace.toolCalls).toHaveLength(3);
    expect(captured?.toolCalls).toHaveLength(3);
    expect(result.reply).toBe('');
  });

  it('keeps failed tool calls and returns an internal customer-sendable draft when evidence is missing', async () => {
    mocked.messages = []; mocked.prompts = []; mocked.subscriber = undefined;
    mocked.executeRecovery = () => {
      const args = { actionId: 'custom_mcp.call_tool', connectionName: 'dify-db', input: {} };
      mocked.subscriber?.({ type: 'tool_execution_start', toolCallId: 'bad-sql', toolName: 'execute_action', args });
      mocked.subscriber?.({ type: 'tool_execution_end', toolCallId: 'bad-sql', toolName: 'execute_action', args,
        result: { isError: true, content: [{ type: 'text', text: 'Tool error: malformed SQL' }] }, isError: true });
    };
    let captured: AgentTracePayload | undefined;
    const result = await runPiSupportAgent({
      ticketId: 3726, customerMessage: '看下这个工单用户的教育优惠会员，以及为什么积分不刷新',
      tools: [], apiKey: 'test-key', modelBaseUrl: 'https://example.test/v1',
      publicReplyAllowed: false, onTrace: trace => { captured = trace; },
    });
    expect(mocked.prompts).toHaveLength(2);
    expect(captured?.toolCalls).toMatchObject([{ toolCallId: 'bad-sql', status: 'failed' }]);
    expect(result.reply).toBe('');
    expect(result.customerReply).toContain('human support engineer');
    expect(result.customerReply).toContain('workspace\'s billing or credit records');
    expect(result.customerReply).not.toContain('here is what we verified');
    const internalDraft = buildInternalDraftBody('job-3726', result.customerReply);
    expect(internalDraft).toContain('Not sent to the customer.');
    expect(internalDraft).toContain(result.customerReply);
    expect(internalDraft).not.toContain('Evidence checked.');
  });

  it('returns a public uncertainty reply rather than blocking a customer-origin ticket', async () => {
    mocked.messages = []; mocked.prompts = []; mocked.subscriber = undefined;
    mocked.executeRecovery = undefined;
    const result = await runPiSupportAgent({
      ticketId: 3726, customerMessage: 'Does the product feature support this workflow?',
      tools: [], apiKey: 'test-key', modelBaseUrl: 'https://example.test/v1',
      publicReplyAllowed: true,
    });
    expect(mocked.prompts).toHaveLength(2);
    expect(result.customerReply).toContain('applicable product guidance');
    expect(result.customerReply).toContain('human support engineer');
    expect(result.customerReply).not.toContain('workspace ID');
    expect(result.reply).toContain(result.customerReply);
    expect(result.reply).toContain('This reply was sent by Dify AI Agent');
    expect(result.reply).not.toContain('here is what we verified');
  });
});
