import { describe, expect, it } from 'vitest';
import { captureAgentRound, sumRoundTokens, updateAgentTool } from './round-telemetry.js';

describe('Pi round telemetry', () => {
  it('counts cache tokens as input and keeps visible text without thinking or tool arguments', () => {
    const round = captureAgentRound({
      role: 'assistant', model: 'requested-model', responseModel: 'model-a', stopReason: 'toolUse',
      usage: { input: 10, output: 4, cacheRead: 20, cacheWrite: 5, reasoning: 2 },
      content: [{ type: 'thinking', thinking: 'private' }, { type: 'text', text: 'Model analysis' }, { type: 'toolCall', id: 'call-1', name: 'get_ticket', arguments: { secret: 'private' } }],
    }, 1, 250);
    expect(round).toEqual({
      round: 1, model: 'model-a', inputTokens: 35, outputTokens: 4,
      cacheReadTokens: 20, cacheWriteTokens: 5, reasoningTokens: 2,
      durationMs: 250, stopReason: 'toolUse',
      assistantContent: 'Model analysis', toolCallCount: 1,
      toolCalls: [{ toolCallId: 'call-1', toolName: 'get_ticket', status: 'unknown', durationMs: null }],
    });
    expect(JSON.stringify(round)).not.toContain('private');
  });

  it('keeps unavailable usage unknown rather than inventing zero tokens', () => {
    const round = captureAgentRound({ role: 'assistant', usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }, 1, 0);
    expect(round?.inputTokens).toBeNull();
    expect(round?.outputTokens).toBeNull();
    expect(sumRoundTokens([round!])).toEqual({ input: null, output: null });
  });


  it('ignores non-assistant messages and sums only observed usage', () => {
    expect(captureAgentRound({ role: 'toolResult', usage: { input: 5 } }, 1, 4)).toBeNull();
    const rounds = [
      captureAgentRound({ role: 'assistant', usage: { input: 3, output: 2, cacheRead: 0, cacheWrite: 0 } }, 1, 4)!,
      captureAgentRound({ role: 'assistant', usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }, 2, 4)!,
    ];
    expect(sumRoundTokens(rounds)).toEqual({ input: 3, output: 2 });
  });

  it('records a bounded OpenConnector action label and terminal tool status without arguments', () => {
    const round = captureAgentRound({
      role: 'assistant', content: [{
        type: 'toolCall', id: 'call-2', name: 'execute_action',
        arguments: { actionId: 'custom_mcp.call_tool', connectionName: 'zendesk',
          input: { toolName: 'get_ticket', arguments: { api_key: 'private-key' } } },
      }],
    }, 1, 5, new Map([['call-2', { status: 'succeeded' as const, durationMs: 17 }]]))!;
    expect(round.toolCalls[0]?.toolName).toBe('zendesk.get_ticket');
    expect(updateAgentTool([round], 'call-2', 'succeeded', 17)).toBe(true);
    expect(round.toolCalls[0]).toEqual({
      toolCallId: 'call-2', toolName: 'zendesk.get_ticket', status: 'succeeded', durationMs: 17,
    });
    expect(JSON.stringify(round)).not.toContain('private-key');
    expect(updateAgentTool([round], 'missing', 'failed', 1)).toBe(false);
  });

  it('attaches tool results that arrive before Pi turn_end', () => {
    const observed = new Map([['call-early', { status: 'failed' as const, durationMs: 23 }]]);
    const round = captureAgentRound({
      role: 'assistant', content: [{ type: 'toolCall', id: 'call-early', name: 'search_tickets' }],
    }, 1, 40, observed)!;
    expect(round.toolCalls[0]).toEqual({
      toolCallId: 'call-early', toolName: 'search_tickets', status: 'failed', durationMs: 23,
    });
  });

  it('does not copy an unsafe dynamic action name into telemetry', () => {
    const round = captureAgentRound({ role: 'assistant', content: [{
      type: 'toolCall', id: 'call-3', name: 'execute_action',
      arguments: { actionId: 'secret token with spaces' },
    }] }, 1, null)!;
    expect(round.toolCalls[0]?.toolName).toBe('execute_action');
  });

  it('does not expose a rejected dynamic tool name', () => {
    const round = captureAgentRound({ role: 'assistant', content: [{
      type: 'toolCall', id: 'call-rejected', name: 'execute_action',
      arguments: { actionId: 'custom_mcp.call_tool', connectionName: 'zendesk',
        input: { toolName: 'potential_secret' } },
    }] }, 1, null, new Map([['call-rejected', { status: 'failed' as const, durationMs: 1 }]]))!;
    expect(round.toolCalls[0]?.toolName).toBe('execute_action');
    expect(JSON.stringify(round)).not.toContain('potential_secret');
  });
});
