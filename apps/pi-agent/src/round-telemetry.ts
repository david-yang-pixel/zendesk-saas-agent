/** Bounded, content-free telemetry for one completed Pi assistant turn. */
export interface AgentRoundTelemetry {
  round: number;
  model: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  reasoningTokens: number | null;
  durationMs: number | null;
  stopReason: string | null;
  toolCallCount: number;
}

function tokenCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export function captureAgentRound(message: unknown, round: number, durationMs: number | null): AgentRoundTelemetry | null {
  if (!message || typeof message !== 'object' || (message as any).role !== 'assistant') return null;
  const assistant = message as Record<string, any>;
  const usage = assistant.usage && typeof assistant.usage === 'object' ? assistant.usage : null;
  const input = tokenCount(usage?.input);
  const output = tokenCount(usage?.output);
  const cacheRead = tokenCount(usage?.cacheRead);
  const cacheWrite = tokenCount(usage?.cacheWrite);
  const hasUsage = [input, output, cacheRead, cacheWrite].some(value => value !== null && value > 0);
  const content = Array.isArray(assistant.content) ? assistant.content : [];
  const model = typeof assistant.responseModel === 'string' ? assistant.responseModel : assistant.model;
  return {
    round,
    model: typeof model === 'string' ? model.slice(0, 100) : null,
    inputTokens: hasUsage ? (input ?? 0) + (cacheRead ?? 0) + (cacheWrite ?? 0) : null,
    outputTokens: hasUsage ? output : null,
    cacheReadTokens: hasUsage ? cacheRead : null,
    cacheWriteTokens: hasUsage ? cacheWrite : null,
    reasoningTokens: hasUsage ? tokenCount(usage?.reasoning) : null,
    durationMs: tokenCount(durationMs),
    stopReason: typeof assistant.stopReason === 'string' ? assistant.stopReason.slice(0, 50) : null,
    toolCallCount: content.filter(part => part?.type === 'toolCall' || part?.type === 'tool_call').length,
  };
}

export function sumRoundTokens(rounds: AgentRoundTelemetry[]): { input: number | null; output: number | null } {
  return {
    input: rounds.some(round => round.inputTokens !== null)
      ? rounds.reduce((sum, round) => sum + (round.inputTokens ?? 0), 0) : null,
    output: rounds.some(round => round.outputTokens !== null)
      ? rounds.reduce((sum, round) => sum + (round.outputTokens ?? 0), 0) : null,
  };
}
