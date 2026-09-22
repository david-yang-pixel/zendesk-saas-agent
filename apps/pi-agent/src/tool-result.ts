export interface ToolOutcome {
  status: 'succeeded' | 'failed' | 'unknown';
  data: any;
  error?: string;
}

/** Unwrap Pi/MCP envelopes once at the recording boundary, preserving failures. */
export function normalizeToolResult(value: any, isError = false): ToolOutcome {
  let failed = isError;
  let error: string | undefined;
  const seen = new Set<object>();
  for (let depth = 0; depth < 12; depth++) {
    if (typeof value === 'string') {
      try { value = JSON.parse(value); continue; } catch {
        if (/^Tool error:/i.test(value)) { failed = true; error = value; }
        return { status: failed ? 'failed' : value.trim() ? 'succeeded' : 'unknown', data: value, error };
      }
    }
    if (value == null) return { status: failed ? 'failed' : 'unknown', data: null, error };
    if (typeof value !== 'object') return { status: failed ? 'failed' : 'succeeded', data: value, error };
    if (seen.has(value)) break;
    seen.add(value);
    if (value.isError === true || value.ok === false || value.success === false ||
        (value.error != null && value.data === undefined)) {
      failed = true;
      error = String(value.error?.message || value.message || value.error || 'Tool execution failed');
    }
    if (value.details != null) { value = value.details; continue; }
    if (value.structuredContent != null) { value = value.structuredContent; continue; }
    if (Array.isArray(value.content)) {
      const text = value.content.filter((b: any) => b?.type === 'text').map((b: any) => b.text).join('\n');
      if (text) { value = text; continue; }
      return { status: failed ? 'failed' : value.content.length ? 'succeeded' : 'unknown', data: value.content, error };
    }
    return { status: failed ? 'failed' : 'succeeded', data: value.data ?? value, error };
  }
  return { status: 'failed', data: null, error: 'Tool result nesting exceeds the supported limit' };
}

export interface RecordedToolCall {
  toolCallId: string;
  toolName: string;
  service: string;
  arguments: any;
  result: any;
  status?: string;
  outcome?: ToolOutcome;
}

/** Merge every live call, including calls removed by partial history compaction. */
export function mergeToolCalls(history: RecordedToolCall[], live: Iterable<RecordedToolCall>): RecordedToolCall[] {
  const calls = new Map(history.map(call => [call.toolCallId, call]));
  for (const call of live) {
    const previous = calls.get(call.toolCallId);
    calls.set(call.toolCallId, { ...previous, ...call, result: call.result ?? previous?.result ?? null });
  }
  return [...calls.values()].map(call => {
    const outcome = normalizeToolResult(call.result, call.status === 'failed');
    return { ...call, status: outcome.status, outcome };
  });
}
