export interface AgentTraceStep {
  type: 'tool_call' | 'answer';
  toolCallId?: string;
  toolName?: string;
  service?: string;
  arguments?: unknown;
  result?: unknown;
  text?: string;
}

interface ToolCallTrace {
  toolCallId: string;
  toolName: string;
  service: string;
  arguments: unknown;
  result: unknown;
}

import { inferOpenConnectorService } from './openconnector.js';

export function inferToolService(toolName: string, args?: unknown): string {
  const openConnectorService = inferOpenConnectorService(toolName, args);
  if (openConnectorService) return openConnectorService;
  if (toolName === 'db_gateway_query') return 'db-gateway';
  if (toolName.startsWith('feishu_')) return 'feishu';
  if (toolName.startsWith('signoz_') || toolName === 'sz_run') return 'signoz';
  if (toolName.startsWith('official_docs_')) return 'docs';
  if (toolName.startsWith('github_')) return 'github';
  if (toolName.startsWith('source_') || toolName.startsWith('repo_') || toolName === 'list_repo_tags' || toolName === 'refresh_repos') return 'source';
  // Relay tools can also start with get_, list_, or search_.
  if (toolName.includes('browser') || toolName.includes('relay') || toolName === 'send_zendesk_native_message') return 'zendesk-browser-relay';
  if (toolName.startsWith('get_') || toolName.startsWith('list_') || toolName.startsWith('search_') || toolName.startsWith('download_') || toolName.startsWith('add_') || toolName.startsWith('update_')) return 'zendesk';
  return 'other';
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function isToolCall(part: Record<string, unknown>): boolean {
  return part.type === 'toolCall' || part.type === 'tool_call' || part.type === 'tool_use' || part.type === 'toolUse';
}

function textParts(content: unknown): string[] {
  if (typeof content === 'string') return [content];
  if (!Array.isArray(content)) return [];
  return content.flatMap((value) => {
    const part = asRecord(value);
    return part?.type === 'text' && typeof part.text === 'string' ? [part.text] : [];
  });
}

function toolResultContent(content: unknown): unknown {
  const texts = textParts(content);
  if (texts.length === 0) {
    // Pi tool results may contain images; never copy model thinking blocks.
    return Array.isArray(content)
      ? content.filter((value) => asRecord(value)?.type === 'image')
      : content ?? null;
  }
  const text = texts.join('\n');
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function completedAnswer(message: Record<string, unknown> | undefined): string {
  if (message?.role !== 'assistant') return '';
  const stopReason = message.stopReason ?? message.stop_reason;
  // Missing stopReason is allowed for legacy messages. Truncated, pending,
  // failed, and tool-use messages are not completed customer replies.
  if (stopReason !== undefined && stopReason !== 'stop') return '';
  if (message.errorMessage) return '';
  if (Array.isArray(message.content) && message.content.some((value) => {
    const part = asRecord(value);
    return part !== undefined && isToolCall(part);
  })) return '';
  return textParts(message.content).join('\n');
}

export function extractAgentTrace(messages: readonly unknown[]): {
  steps: AgentTraceStep[];
  toolCalls: ToolCallTrace[];
  finalAnswer: string;
} {
  const results = new Map<string, unknown>();
  for (const value of messages) {
    const message = asRecord(value);
    if (message?.role !== 'toolResult' && message?.role !== 'tool_result' && message?.role !== 'tool') continue;
    const callId = message.toolCallId ?? message.tool_call_id ?? message.tool_use_id ?? message.id;
    if (typeof callId === 'string') results.set(callId, toolResultContent(message.content));
  }

  const steps: AgentTraceStep[] = [];
  const toolCalls: ToolCallTrace[] = [];
  for (const value of messages) {
    const message = asRecord(value);
    if (message?.role !== 'assistant') continue;
    if (typeof message.content === 'string') {
      steps.push({ type: 'answer', text: message.content });
      continue;
    }
    if (!Array.isArray(message.content)) continue;
    for (const value of message.content) {
      const part = asRecord(value);
      if (!part) continue;
      if (isToolCall(part) && typeof part.id === 'string' && typeof part.name === 'string') {
        const args = part.arguments ?? part.args ?? part.input ?? null;
        const call: ToolCallTrace = {
          toolCallId: part.id,
          toolName: part.name,
          service: inferToolService(part.name, args),
          arguments: args,
          result: results.get(part.id) ?? null,
        };
        toolCalls.push(call);
        steps.push({ type: 'tool_call', ...call });
      } else if (part.type === 'text' && typeof part.text === 'string') {
        steps.push({ type: 'answer', text: part.text });
      }
    }
  }

  // Never search backwards: a trailing tool result or failed assistant turn
  // must not turn earlier investigation text into a final customer reply.
  return { steps, toolCalls, finalAnswer: completedAnswer(asRecord(messages.at(-1))) };
}
