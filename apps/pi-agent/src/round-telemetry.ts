/** Telemetry and visible assistant text for one completed Pi assistant turn. */
export interface AgentToolTelemetry {
  toolCallId: string;
  toolName: string;
  status: 'unknown' | 'running' | 'succeeded' | 'failed';
  durationMs: number | null;
}

export type CompletedToolTelemetry = Pick<AgentToolTelemetry, 'status' | 'durationMs'>;

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
  promptContent: string | null;
  assistantContent: string | null;
  toolCallCount: number;
  toolCalls: AgentToolTelemetry[];
}

const SAFE_TOOL_NAME = /^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$/;

export function recordProviderPayload(
  payload: unknown, round: number, target: Map<number, string>,
): boolean {
  if (round <= 0) return false;
  try {
    const serialized = JSON.stringify(payload);
    if (typeof serialized !== 'string') return false;
    target.set(round, serialized);
    return true;
  } catch {
    return false;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function safeToolName(value: unknown): string | null {
  return typeof value === 'string' && SAFE_TOOL_NAME.test(value) ? value : null;
}

function displayToolName(name: unknown, argumentsValue: unknown): string {
  const outer = safeToolName(name);
  if (outer !== 'execute_action') return outer ?? 'unknown_tool';
  const args = asRecord(argumentsValue);
  const action = safeToolName(args?.actionId);
  if (action === 'custom_mcp.call_tool') {
    const input = asRecord(args?.input);
    const connection = safeToolName(args?.connectionName);
    const remote = safeToolName(input?.toolName);
    const label = connection && remote ? `${connection}.${remote}` : null;
    return label && label.length <= 100 ? label : outer;
  }
  return action?.includes('.') ? action : outer;
}

function tokenCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export function captureAgentRound(
  message: unknown, round: number, durationMs: number | null,
  completedTools: ReadonlyMap<string, CompletedToolTelemetry> = new Map(),
  promptContent: string | null = null,
): AgentRoundTelemetry | null {
  if (!message || typeof message !== 'object' || (message as any).role !== 'assistant') return null;
  const assistant = message as Record<string, any>;
  const usage = assistant.usage && typeof assistant.usage === 'object' ? assistant.usage : null;
  const input = tokenCount(usage?.input);
  const output = tokenCount(usage?.output);
  const cacheRead = tokenCount(usage?.cacheRead);
  const cacheWrite = tokenCount(usage?.cacheWrite);
  const hasUsage = [input, output, cacheRead, cacheWrite].some(value => value !== null && value > 0);
  const content = Array.isArray(assistant.content) ? assistant.content : [];
  const assistantContent = typeof assistant.content === 'string'
    ? assistant.content
    : content.filter(part => part?.type === 'text' && typeof part.text === 'string')
      .map(part => part.text).join('\n');
  const toolParts = content.filter(part =>
    part?.type === 'toolCall' || part?.type === 'tool_call' || part?.type === 'toolUse' || part?.type === 'tool_use',
  );
  const toolCalls = toolParts.slice(0, 100).flatMap(part => {
    const callId = part?.id ?? part?.toolCallId ?? part?.call_id;
    if (typeof callId !== 'string' || !callId || callId.length > 255) return [];
    const completed = completedTools.get(callId);
    const outerName = safeToolName(part?.name ?? part?.toolName) ?? 'unknown_tool';
    return [{
      toolCallId: callId,
      // Dynamic action labels come from tool arguments; use them only after success.
      toolName: completed?.status === 'succeeded'
        ? displayToolName(part?.name ?? part?.toolName, part?.arguments ?? part?.args ?? part?.input)
        : outerName,
      status: completed?.status ?? 'unknown' as const,
      durationMs: completed?.durationMs ?? null,
    }];
  });
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
    promptContent,
    assistantContent: assistantContent || null,
    toolCallCount: toolParts.length,
    toolCalls,
  };
}

export function updateAgentTool(
  rounds: AgentRoundTelemetry[], callId: string,
  status: AgentToolTelemetry['status'], durationMs: number | null,
): boolean {
  for (const round of rounds) {
    const call = round.toolCalls.find(item => item.toolCallId === callId);
    if (!call) continue;
    call.status = status;
    call.durationMs = tokenCount(durationMs);
    return true;
  }
  return false;
}

export function sumRoundTokens(rounds: AgentRoundTelemetry[]): { input: number | null; output: number | null } {
  return {
    input: rounds.some(round => round.inputTokens !== null)
      ? rounds.reduce((sum, round) => sum + (round.inputTokens ?? 0), 0) : null,
    output: rounds.some(round => round.outputTokens !== null)
      ? rounds.reduce((sum, round) => sum + (round.outputTokens ?? 0), 0) : null,
  };
}
