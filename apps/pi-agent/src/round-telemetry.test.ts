import { describe, expect, it } from 'vitest';
import { captureAgentRound, sumRoundTokens } from './round-telemetry.js';

describe('Pi round telemetry', () => {
  it('counts cache tokens as input and excludes message content', () => {
    const round = captureAgentRound({
      role: 'assistant', model: 'requested-model', responseModel: 'model-a', stopReason: 'toolUse',
      usage: { input: 10, output: 4, cacheRead: 20, cacheWrite: 5, reasoning: 2 },
      content: [{ type: 'thinking', thinking: 'private' }, { type: 'toolCall', arguments: { secret: 'private' } }],
    }, 1, 250);
    expect(round).toEqual({
      round: 1, model: 'model-a', inputTokens: 35, outputTokens: 4,
      cacheReadTokens: 20, cacheWriteTokens: 5, reasoningTokens: 2,
      durationMs: 250, stopReason: 'toolUse', toolCallCount: 1,
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
});
