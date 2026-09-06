import type { AssistantMessage, ToolResultMessage } from '@earendil-works/pi-ai';
import { describe, expect, it } from 'vitest';
import { extractAgentTrace, inferToolService } from './trace.js';

function assistant(
  content: AssistantMessage['content'],
  stopReason: AssistantMessage['stopReason'] = 'stop',
): AssistantMessage {
  return {
    role: 'assistant', content, stopReason,
    api: 'openai-responses', provider: 'openai', model: 'test-model', timestamp: 1,
    usage: {
      input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
  };
}

function result(
  toolCallId: string,
  content: ToolResultMessage['content'],
  isError = false,
): ToolResultMessage {
  return { role: 'toolResult', toolCallId, toolName: 'db_gateway_query', content, isError, timestamp: 2 };
}

describe('extractAgentTrace', () => {
  it('records Pi SDK toolCall/toolResult messages and correlates calls by ID, not tool name or result order', () => {
    const messages = [
      assistant([
        { type: 'text', text: 'I will check the installed plugin.' },
        { type: 'toolCall', id: 'call-installed', name: 'db_gateway_query', arguments: { database: 'plugin', query: 'installed version' } },
        { type: 'toolCall', id: 'call-previous', name: 'db_gateway_query', arguments: { database: 'plugin', query: 'previous version' } },
      ], 'toolUse'),
      result('call-previous', [{ type: 'text', text: '{"version":"previous"}' }]),
      result('call-installed', [{ type: 'text', text: '[{"version":"current"}]' }]),
      assistant([{ type: 'text', text: 'Here are the verified recovery steps.' }]),
    ] satisfies readonly (AssistantMessage | ToolResultMessage)[];

    const trace = extractAgentTrace(messages);
    expect(trace.toolCalls).toEqual([
      {
        toolCallId: 'call-installed', toolName: 'db_gateway_query', service: 'db-gateway',
        arguments: { database: 'plugin', query: 'installed version' }, result: [{ version: 'current' }],
      },
      {
        toolCallId: 'call-previous', toolName: 'db_gateway_query', service: 'db-gateway',
        arguments: { database: 'plugin', query: 'previous version' }, result: { version: 'previous' },
      },
    ]);
    expect(trace.steps.filter((step) => step.type === 'tool_call')).toEqual(
      trace.toolCalls.map((call) => ({ type: 'tool_call', ...call })),
    );
    expect(trace.finalAnswer).toBe('Here are the verified recovery steps.');
  });

  it.each(['toolCallId', 'tool_call_id', 'id'])('supports legacy tool_call/tool_result with %s result IDs', (idField) => {
    const trace = extractAgentTrace([
      { role: 'assistant', content: [{ type: 'tool_call', id: 'legacy', name: 'get_ticket', arguments: { ticket_id: 4304 } }] },
      { role: 'tool_result', [idField]: 'legacy', content: 'Ticket data' },
      { role: 'assistant', content: 'A completed legacy reply.' },
    ]);
    expect(trace.toolCalls).toEqual([{
      toolCallId: 'legacy', toolName: 'get_ticket', service: 'zendesk',
      arguments: { ticket_id: 4304 }, result: 'Ticket data',
    }]);
    expect(trace.finalAnswer).toBe('A completed legacy reply.');
  });

  it('keeps every tool result text part and joins all final text parts without retaining thinking or signatures', () => {
    const trace = extractAgentTrace([
      assistant([
        { type: 'thinking', thinking: 'PRIVATE_REASONING', thinkingSignature: 'PRIVATE_SIGNATURE' },
        { type: 'toolCall', id: 'call', name: 'db_gateway_query', arguments: {}, thoughtSignature: 'PRIVATE_SIGNATURE' },
      ], 'toolUse'),
      result('call', [{ type: 'text', text: 'First result' }, { type: 'text', text: 'Second result' }]),
      assistant([
        { type: 'thinking', thinking: 'PRIVATE_REASONING', thinkingSignature: 'PRIVATE_SIGNATURE' },
        { type: 'text', text: 'Step one.', textSignature: 'PRIVATE_SIGNATURE' },
        { type: 'text', text: 'Step two.' },
      ]),
    ]);
    expect(trace.toolCalls[0].result).toBe('First result\nSecond result');
    expect(trace.finalAnswer).toBe('Step one.\nStep two.');
    expect(JSON.stringify(trace)).not.toMatch(/PRIVATE_REASONING|PRIVATE_SIGNATURE|thinking/);
  });

  it.each(['error', 'aborted', 'toolUse', 'length', 'pending', 'deferred'] as const)(
    'does not use partial text or fall back to earlier text after %s', (stopReason) => {
      const trace = extractAgentTrace([
        assistant([{ type: 'text', text: 'Earlier investigation text.' }]),
        assistant([{ type: 'text', text: 'Incomplete text.' }], stopReason),
      ]);
      expect(trace.finalAnswer).toBe('');
    },
  );

  it('rejects a terminal assistant error even if stopReason is absent', () => {
    const trace = extractAgentTrace([
      { role: 'assistant', content: 'Earlier investigation text.' },
      { role: 'assistant', content: 'Partial reply.', errorMessage: 'Request failed' },
    ]);
    expect(trace.finalAnswer).toBe('');
  });

  it.each(['toolCall', 'tool_call'])('does not treat text alongside a terminal %s as a final reply', (type) => {
    const trace = extractAgentTrace([
      { role: 'assistant', content: 'Earlier investigation text.' },
      { role: 'assistant', content: [{ type: 'text', text: 'Checking now.' }, { type, id: 'pending-call', name: 'sz_run', arguments: {} }] },
    ]);
    expect(trace.finalAnswer).toBe('');
    expect(trace.toolCalls[0].result).toBeNull();
  });

  it.each([false, true])('does not reuse text when a tool result has no following answer (isError=%s)', (isError) => {
    const trace = extractAgentTrace([
      assistant([{ type: 'text', text: 'Earlier investigation text.' }]),
      assistant([{ type: 'toolCall', id: 'call', name: 'db_gateway_query', arguments: {} }], 'toolUse'),
      result('call', [{ type: 'text', text: isError ? 'Tool failed' : 'Tool completed' }], isError),
    ]);
    expect(trace.finalAnswer).toBe('');
    expect(trace.toolCalls[0].result).toBe(isError ? 'Tool failed' : 'Tool completed');
  });

  it('can use a completed answer after recovering from a tool error', () => {
    const trace = extractAgentTrace([
      assistant([{ type: 'toolCall', id: 'call', name: 'db_gateway_query', arguments: {} }], 'toolUse'),
      result('call', [{ type: 'text', text: 'Tool failed' }], true),
      assistant([{ type: 'text', text: 'Please provide the time of the failed request so we can continue.' }]),
    ]);
    expect(trace.finalAnswer).toBe('Please provide the time of the failed request so we can continue.');
  });

  it('does not reuse an old answer after a new user message or a thinking-only assistant message', () => {
    const earlier = assistant([{ type: 'text', text: 'An earlier answer.' }]);
    expect(extractAgentTrace([earlier, { role: 'user', content: 'A new question.' }]).finalAnswer).toBe('');
    expect(extractAgentTrace([earlier, assistant([{ type: 'thinking', thinking: 'PRIVATE_REASONING' }])]).finalAnswer).toBe('');
  });

  it('handles empty and malformed messages without inventing calls or answers', () => {
    expect(extractAgentTrace([])).toEqual({ steps: [], toolCalls: [], finalAnswer: '' });
    expect(extractAgentTrace([
      null, undefined, 42,
      { role: 'assistant', content: [null, { type: 'toolCall' }, { type: 'text', text: 42 }] },
    ])).toEqual({ steps: [], toolCalls: [], finalAnswer: '' });
  });
});

describe('inferToolService', () => {
  it.each([
    ['db_gateway_query', 'db-gateway'],
    ['feishu_doc_read', 'feishu'],
    ['signoz_query', 'signoz'],
    ['sz_run', 'signoz'],
    ['official_docs_search', 'docs'],
    ['github_search', 'github'],
    ['source_search', 'source'],
    ['repo_read', 'source'],
    ['list_repo_tags', 'source'],
    ['refresh_repos', 'source'],
    ['get_browser_state', 'zendesk-browser-relay'],
    ['list_relay_sessions', 'zendesk-browser-relay'],
    ['send_zendesk_native_message', 'zendesk-browser-relay'],
    ['get_ticket', 'zendesk'],
    ['search_tickets', 'zendesk'],
    ['download_attachment', 'zendesk'],
    ['add_ticket_note', 'zendesk'],
    ['update_ticket', 'zendesk'],
    ['custom_tool', 'other'],
  ])('classifies %s as %s', (name, service) => {
    expect(inferToolService(name)).toBe(service);
  });
});
