import {
  ModelRuntime,
  createAgentSession,
  SessionManager,
  DefaultResourceLoader,
  getAgentDir,
} from '@earendil-works/pi-coding-agent';
import { CustomTool } from './mcp.js';
import { createProjectSkillReadTool, getProjectSkills } from './project-skills.js';
import { inferOpenConnectorService } from './openconnector.js';
import { mergeToolCalls, normalizeToolResult, type ToolOutcome } from './tool-result.js';
import { captureAgentRound, sumRoundTokens, updateAgentTool, type AgentRoundTelemetry, type CompletedToolTelemetry } from './round-telemetry.js';

export interface AgentAttachment {
  id: string;
  name: string;
  url?: string;
  contentType?: string;
}

export interface AgentRunOptions {
  ticketId: number;
  customerMessage: string;
  requesterEmail?: string;
  tools: CustomTool[];
  modelName?: string;
  modelBaseUrl?: string;
  apiKey?: string;
  abortSignal?: AbortSignal;
  attachments?: AgentAttachment[];
  contextPrepared?: boolean;
  publicReplyAllowed?: boolean;
  onRoundTelemetry?: (rounds: readonly AgentRoundTelemetry[]) => void;
  onTrace?: (trace: AgentTracePayload) => void;
}

export interface AgentTraceStep {
  type: 'thinking' | 'tool_call' | 'answer';
  thinking?: string;
  toolCallId?: string;
  toolName?: string;
  service?: string;
  arguments?: any;
  result?: any;
  text?: string;
}

export interface AgentTracePayload {
  ticketId?: number;
  requesterEmail?: string;
  customerMessage?: string;
  model?: string;
  steps: AgentTraceStep[];
  thinking: string[];
  toolCalls: Array<{
    toolCallId: string;
    toolName: string;
    service: string;
    arguments: any;
    result: any;
    status?: string;
    outcome?: ToolOutcome;
  }>;
  finalReply: string;
  totalDurationMs: number;
  rounds: AgentRoundTelemetry[];
  tokens?: {
    input: number | null;
    output: number | null;
  };
  startedAt: string;
  finishedAt: string;
}

export interface AgentRunResult {
  reply: string;
  customerReply: string;
  internalNote?: string;
  trace: AgentTracePayload;
}

export function supportsExplicitReasoningEffort(baseUrl: string): boolean {
  try {
    const hostname = new URL(baseUrl).hostname.toLowerCase();
    return hostname !== 'api.tokener.dev' && !hostname.endsWith('.tokener.dev');
  } catch {
    return true;
  }
}

export function getTerminalAssistantError(messages: readonly unknown[]): string | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index];
    if (!message || typeof message !== 'object' || Array.isArray(message)) continue;
    const record = message as Record<string, unknown>;
    if (record.role !== 'assistant') continue;
    if (record.stopReason !== 'error' && record.stop_reason !== 'error' && !record.errorMessage) {
      return undefined;
    }
    return typeof record.errorMessage === 'string' && record.errorMessage.trim()
      ? record.errorMessage.trim()
      : 'The model provider ended the request with an error.';
  }
  return undefined;
}

export function inferToolService(toolName: string, args?: unknown): string {
  const openConnectorService = inferOpenConnectorService(toolName, args);
  if (openConnectorService) return openConnectorService;
  if (toolName === 'db_gateway_query') return 'db-gateway';
  if (toolName.startsWith('feishu_')) return 'feishu';
  if (toolName.startsWith('signoz_') || toolName === 'sz_run') return 'signoz';
  if (toolName.startsWith('official_docs_')) return 'docs';
  if (toolName.startsWith('github_')) return 'github';
  if (toolName.startsWith('source_') || toolName.startsWith('repo_') || toolName === 'list_repo_tags' || toolName === 'refresh_repos') return 'source';
  if (toolName.startsWith('get_') || toolName.startsWith('list_') || toolName.startsWith('search_') || toolName.startsWith('download_') || toolName.startsWith('add_') || toolName.startsWith('update_')) return 'zendesk';
  if (toolName.includes('browser') || toolName.includes('relay') || toolName === 'send_zendesk_native_message') return 'zendesk-browser-relay';
  return 'other';
}

export const DIFY_SUPPORT_SYSTEM_PROMPT = `
You are the Dify SaaS support investigator for Zendesk tickets. Read the dify-saas-support skill before investigating; load other listed skills only when relevant.
Investigate only through OpenConnector read operations. Never use Gmail or perform any external mutation. The Worker alone decides delivery: customer messages may receive a public reply through Zendesk Browser Reply, while operator mentions receive an internal customer-reply draft. Internal analysis stays in the trace.
Ground claims in current evidence. If evidence is unavailable, still answer a customer-origin ticket in English: name what remains unverified and what the customer can provide or a human support engineer must check. Never invent a conclusion. Return exactly two sections: === INTERNAL NOTE === for factual internal findings, then === CUSTOMER REPLY === for a concise, customer-safe English reply.
`.trim();

export function sanitizeCustomerReply(rawReply: string): string {
  if (!rawReply) return '';
  let text = rawReply.trim();

  // Strip leading internal investigation headers like "## Investigation update — ticket #4396" or "Investigation update:"
  text = text.replace(/^#*\s*Investigation\s+update(?::|\s*[-—:]\s*(?:ticket\s*#?\d+)?|\s*[-—:])?\s*\n+/i, '');
  text = text.replace(/^#*\s*Ticket\s*#?\d+\s*Investigation(?:\s+Update)?(?::|\s*[-—:])?\s*\n+/i, '');

  // Strip leading "Suggested Reply:" / "### Suggested Reply:" / "Customer Reply:" wrapper headers
  text = text.replace(/^#*\s*(?:Suggested\s+Reply|Customer\s+Reply|Public\s+Reply)(?::|\s*[-—:])?\s*\n+/i, '');

  // Truncate if internal analysis or internal memos follow (e.g. "🔍 Internal Analysis:", "--- \n### Internal Note:", etc.)
  const internalCutoffRegex = /(?:\n\s*[-—_]{3,}\s*)?\n\s*(?:#*\s*(?:🔍\s*)?Internal\s+Analysis|#*\s*Internal\s+(?:Note|Memo|Investigation)|#*\s*(?:🤖\s*)?\[?Dify(?:\s+AI)?(?:\s+Support)?(?:\s+Agent)?\s*(?:调查备忘录|内部备忘录|内部分析)\]?|#*\s*(?:内部调查备忘录(?:\s*[/／]\s*排查记录)?|内部排查记录|技术排查备忘录))(?::|\s*[-—:]|\s*\n)/i;
  const cutoffMatch = text.match(internalCutoffRegex);
  if (cutoffMatch && cutoffMatch.index !== undefined) {
    text = text.substring(0, cutoffMatch.index).trim();
  }

  // Convert third-person handover prompts like "Ask Catherine for:" or "Ask the customer for:" to direct address
  text = text.replace(/(?:Ask\s+[A-Za-z0-9_.-]+\s+for|Ask\s+the\s+(?:customer|user)\s+for):\s*/gi, 'Could you please provide: ');
  text = text.replace(/(?:Ask\s+[A-Za-z0-9_.-]+|Ask\s+the\s+(?:customer|user))\s+to\s+/gi, 'Please ');

  return text.trim();
}

export interface DualTrackOutput {
  customerReply: string;
  internalNote?: string;
}

export function extractDualTrackOutputs(rawText: string): DualTrackOutput {
  if (!rawText) return { customerReply: '' };

  const internalMarker = '=== INTERNAL NOTE ===';
  const customerMarker = '=== CUSTOMER REPLY ===';

  const internalIdx = rawText.indexOf(internalMarker);
  const customerIdx = rawText.indexOf(customerMarker);

  if (internalIdx !== -1 && customerIdx !== -1) {
    let internalContent = '';
    let customerContent = '';

    if (internalIdx < customerIdx) {
      internalContent = rawText.substring(internalIdx + internalMarker.length, customerIdx).trim();
      customerContent = rawText.substring(customerIdx + customerMarker.length).trim();
    } else {
      customerContent = rawText.substring(customerIdx + customerMarker.length, internalIdx).trim();
      internalContent = rawText.substring(internalIdx + internalMarker.length).trim();
    }

    return {
      customerReply: sanitizeCustomerReply(customerContent),
      internalNote: internalContent || undefined,
    };
  }

  // Fallback: If only customerMarker is found
  if (customerIdx !== -1) {
    const customerContent = rawText.substring(customerIdx + customerMarker.length).trim();
    return {
      customerReply: sanitizeCustomerReply(customerContent),
    };
  }

  // Fallback: If only internalMarker is found
  if (internalIdx !== -1) {
    const internalContent = rawText.substring(internalIdx + internalMarker.length).trim();
    return {
      customerReply: sanitizeCustomerReply(rawText.substring(0, internalIdx).trim()),
      internalNote: internalContent || undefined,
    };
  }

  // Secondary Fallback: Detect implicit internal analysis sections like "🔍 Internal Analysis" or "### Internal Note"
  const implicitInternalRegex = /(?:\n\s*[-—_]{3,}\s*)?\n\s*(?:#*\s*(?:🔍\s*)?Internal\s+Analysis|#*\s*Internal\s+(?:Note|Memo|Investigation)|#*\s*(?:🤖\s*)?\[?Dify(?:\s+AI)?(?:\s+Support)?(?:\s+Agent)?\s*(?:调查备忘录|内部备忘录|内部分析)\]?|#*\s*(?:内部调查备忘录(?:\s*[/／]\s*排查记录)?|内部排查记录|技术排查备忘录))(?::|\s*[-—:]|\s*\n)/i;
  const implicitMatch = rawText.match(implicitInternalRegex);
  if (implicitMatch && implicitMatch.index !== undefined && implicitMatch.index > 0) {
    const beforeContent = rawText.substring(0, implicitMatch.index).trim();
    const afterContent = rawText.substring(implicitMatch.index).replace(/^\s*[-—_]{3,}\s*/, '').trim();
    return {
      customerReply: sanitizeCustomerReply(beforeContent),
      internalNote: afterContent || undefined,
    };
  }

  // Fallback: If no markers were emitted, sanitize whole text for customer
  return {
    customerReply: sanitizeCustomerReply(rawText),
  };
}

export function validateCustomerDelivery(
  rawText: string,
  customerReply: string,
): void {
  const internalMarkers = rawText.match(/^=== INTERNAL NOTE ===[ \t]*$/gm) ?? [];
  const customerMarkers = rawText.match(/^=== CUSTOMER REPLY ===[ \t]*$/gm) ?? [];
  if (internalMarkers.length !== 1 || customerMarkers.length !== 1) {
    throw new Error('Customer delivery blocked: required dual-track output markers are missing');
  }
  if (!customerReply.trim()) throw new Error('Customer delivery blocked: customer reply is empty');
  if (/[\u3400-\u9fff]/u.test(customerReply)) {
    throw new Error('Customer delivery blocked: public reply must be entirely in English');
  }
  if (/=== (?:INTERNAL NOTE|CUSTOMER REPLY) ===|(?:内部调查|调查结果\s*\(|Internal Analysis|Internal Note)/i.test(customerReply)) {
    throw new Error('Customer delivery blocked: internal analysis leaked into public reply');
  }
}

export function buildEvidenceGapReply(missingSources: readonly string[]): string {
  if (missingSources.length === 0) throw new Error('Evidence gap reply requires at least one missing source');
  const descriptions: Record<string, string> = {
    feishu: 'the current education or discount policy',
    docs: 'the applicable product guidance',
    'db-gateway': 'your workspace\'s billing or credit records',
  };
  const missing = missingSources.map(source => descriptions[source] ?? 'the relevant supporting information');
  const details = missing.length === 1
    ? missing[0]
    : `${missing.slice(0, -1).join(', ')} and ${missing[missing.length - 1]}`;
  const accountDetail = missingSources.includes('db-gateway')
    ? ' If the affected workspace ID and the time of the issue are not already in this ticket, please share them.'
    : '';
  return `I can't yet verify ${details}, so I don't want to give you an unsupported conclusion. A human support engineer needs to check this before confirming the answer.${accountDetail} You can reply “human” to request that review.`;
}

export function getMissingCustomerEvidenceSources(
  customerMessage: string,
  toolCalls: Array<{ toolName: string; service: string; status?: string; arguments?: any }>,
): string[] {
  const successfulEvidence = (service: string) => toolCalls.some(call =>
    call.service === service &&
    call.status === 'succeeded' &&
    call.toolName === 'execute_action' &&
    call.arguments?.actionId !== 'custom_mcp.list_tools',
  );
  const education = /educat(?:ion|ional)?|student|school|university|教育|学生|学校|高校/i.test(customerMessage);
  const creditsOrTerms = /credit|quota|discount|promotion|plan|subscription|积分|额度|优惠|会员|订阅/i.test(customerMessage);
  const productFeature = /产品功能|是否支持|功能|特性|feature|product capability|how (?:does|do|to) .{0,40} work/i.test(customerMessage);
  const accountSpecific = /我的|用户的|该工作区|这个工作区|my |our |this workspace|customer's/i.test(customerMessage);
  const requiredSources = new Set<string>();
  if (education && creditsOrTerms) {
    requiredSources.add('feishu');
    requiredSources.add('docs');
    if (accountSpecific) requiredSources.add('db-gateway');
  }
  if (productFeature) requiredSources.add('docs');
  return [...requiredSources].filter(service => !successfulEvidence(service));
}

export async function runPiSupportAgent(options: AgentRunOptions): Promise<AgentRunResult> {
  if (options.abortSignal?.aborted) {
    const err = new Error(`Agent run aborted before start for ticket #${options.ticketId}`);
    err.name = 'AbortError';
    throw err;
  }

  const modelName = options.modelName || process.env.CASEWORK_MODEL_NAME || 'gpt-5.6-terra';
  const rawBaseUrl = options.modelBaseUrl || process.env.CASEWORK_MODEL_BASE_URL || 'https://api.tokener.dev';
  let baseUrl = rawBaseUrl.replace(/\/+$/, '');
  if (!baseUrl.endsWith('/v1')) {
    baseUrl += '/v1';
  }
  const apiKey = options.apiKey || process.env.CASEWORK_CREDENTIAL_OPENAI_PRIMARY || process.env.OPENAI_API_KEY || '';

  console.log(`[Agent] Initializing ModelRuntime with baseUrl=${baseUrl}, model=${modelName}, apiKeyLen=${apiKey.length}`);

  const runtime = await ModelRuntime.create();
  runtime.registerProvider('openai', {
    baseUrl: baseUrl,
    apiKey: apiKey,
    models: [
      {
        id: modelName,
        name: modelName,
        api: 'openai-responses',
        // Tokener's Responses-compatible endpoint currently rejects the SDK's
        // explicit reasoning.effort field. It still applies its own server-side
        // reasoning default when the field is omitted.
        reasoning: supportsExplicitReasoningEffort(baseUrl),
        input: ['text', 'image'],
        cost: { input: 0, output: 0 },
        contextWindow: 128000,
        maxTokens: 16384,
      } as any,
    ],
  });

  const model = runtime.getModel('openai', modelName);
  if (!model) {
    throw new Error(`Failed to initialize model ${modelName} with provider openai`);
  }

  const projectSkills = getProjectSkills();
  console.log(`[Agent] Registered ${projectSkills.length} project skills:`, projectSkills.map(s => s.name));
  if (!projectSkills.some(skill => skill.name === 'dify-saas-support')) {
    throw new Error('Required dify-saas-support skill is missing');
  }
  const skillReadTool = createProjectSkillReadTool(projectSkills);
  const loader = new DefaultResourceLoader({
    cwd: process.cwd(),
    agentDir: getAgentDir(),
    noContextFiles: true,
    skillsOverride: (current) => ({ skills: projectSkills, diagnostics: current.diagnostics }),
    systemPromptOverride: () => DIFY_SUPPORT_SYSTEM_PROMPT,
    appendSystemPromptOverride: () => [],
  });
  await loader.reload();

  const { session } = await createAgentSession({
    resourceLoader: loader,
    sessionManager: SessionManager.inMemory(),
    modelRuntime: runtime,
    model: model,
    customTools: [...options.tools, skillReadTool],
    tools: [...options.tools.map(t => t.name), 'read'],
  });

  // Wire up immediate abort hook
  if (options.abortSignal) {
    options.abortSignal.addEventListener('abort', async () => {
      console.warn(`[Agent] 🛑 Received abort signal for ticket #${options.ticketId}, aborting session immediately...`);
      try {
        await session.abort();
      } catch (abortErr: any) {
        console.warn(`[Agent] Error during session.abort for ticket #${options.ticketId}:`, abortErr.message);
      }
    }, { once: true });
  }

  const liveExecutedTools = new Map<string, { toolCallId: string; toolName: string; service: string; arguments: any; result: any; status: string }>();
  const completedRounds: AgentRoundTelemetry[] = [];
  const toolStartedAt = new Map<string, number>();
  const completedTools = new Map<string, CompletedToolTelemetry>();
  const emitRoundTelemetry = () => options.onRoundTelemetry?.(
    completedRounds.map(round => ({ ...round, toolCalls: round.toolCalls.map(call => ({ ...call })) })),
  );
  let turnStartedAt: number | null = null;

  session.subscribe((event: any) => {
    if (event.type === 'turn_start') {
      turnStartedAt = Date.now();
    } else if (event.type === 'turn_end') {
      const round = captureAgentRound(
        event.message,
        completedRounds.length + 1,
        turnStartedAt === null ? null : Date.now() - turnStartedAt,
        completedTools,
      );
      if (round) {
        completedRounds.push(round);
        completedTools.clear();
        emitRoundTelemetry();
      }
      turnStartedAt = null;
    } else if (event.type === 'tool_execution_start') {
      const callId = event.toolCallId || `call-${liveExecutedTools.size + 1}`;
      toolStartedAt.set(callId, Date.now());
      if (updateAgentTool(completedRounds, callId, 'running', null)) emitRoundTelemetry();
      console.log(`\n[Agent Tool Call Start] ${event.toolName} (${callId})`);
      liveExecutedTools.set(callId, {
        toolCallId: callId,
        toolName: event.toolName,
        service: inferToolService(event.toolName, event.args),
        arguments: event.args ?? {},
        result: null,
        status: 'running',
      });
    } else if (event.type === 'tool_execution_end') {
      const callId = event.toolCallId || `call-${liveExecutedTools.size}`;
      console.log(`\n[Agent Tool Result End] ${event.toolName} (${callId}): isError=${event.isError}`);
      const started = toolStartedAt.get(callId);
      toolStartedAt.delete(callId);
      const toolStatus = normalizeToolResult(event.result, event.isError).status;
      const toolDuration = started === undefined ? null : Date.now() - started;
      completedTools.set(callId, { status: toolStatus, durationMs: toolDuration });
      if (updateAgentTool(completedRounds, callId, toolStatus, toolDuration)) {
        completedTools.delete(callId);
        emitRoundTelemetry();
      }
      const existing = liveExecutedTools.get(callId);
      if (existing) {
        existing.result = event.result;
        existing.status = toolStatus;
      } else {
        liveExecutedTools.set(callId, {
          toolCallId: callId,
          toolName: event.toolName,
          service: inferToolService(event.toolName, event.args),
          arguments: event.args ?? {},
          result: event.result,
          status: toolStatus,
        });
      }
    } else if (event.type === 'message_update') {
      const sub = event.assistantMessageEvent;
      if (sub?.type === 'text_delta') {
        process.stdout.write(sub.delta);
      } else if (sub?.type === 'toolCall' || sub?.type === 'tool_call') {
        console.log(`\n[Agent Tool Call Update] ${sub.name}`);
      }
    } else if (event.type === 'error') {
      console.error('\n[Agent Error Event]', event.error);
    } else {
      console.log(`\n[Agent Event] ${event.type}`);
    }
  });

  let attachmentSection = '';
  if (options.attachments && options.attachments.length > 0) {
    attachmentSection = '\nCustomer Uploaded Attachments / Screenshots:\n' +
      options.attachments.map(a => `- ${a.name} (type: ${a.contentType || 'unknown'}, URL: ${a.url || 'N/A'})`).join('\n') + '\n';
  }
  const requiredSources = getMissingCustomerEvidenceSources(options.customerMessage, []);

  const prompt = `
Customer Ticket #${options.ticketId}
Requester Email: ${options.requesterEmail || 'Unknown'}
Customer Message:
"""
${options.customerMessage}
"""
${attachmentSection}
${options.contextPrepared ? 'The Worker already prepared the latest conversation and unreplied customer events above.' : ''}
${requiredSources.length ? `Relevant read-only evidence sources to consider: ${requiredSources.join(', ')}. If any are unavailable or inconclusive, still provide a safe English reply that states what remains unverified and the next customer or human-support step.` : ''}

First read the dify-saas-support skill at ${projectSkills.find(skill => skill.name === 'dify-saas-support')!.filePath}. Follow it for investigation and final output; read other relevant skills only when needed.
`.trim();

  const startTime = Date.now();
  const startedAt = new Date().toISOString();

  console.log(`[Agent] Sending prompt to session for ticket #${options.ticketId}...`);
  try {
    await session.prompt(prompt);
  } catch (promptErr: any) {
    if (options.abortSignal?.aborted || (session as any).aborted || promptErr.name === 'AbortError' || promptErr.message?.includes('aborted')) {
      const abortErr = new Error(`Agent execution aborted for ticket #${options.ticketId}`);
      abortErr.name = 'AbortError';
      throw abortErr;
    }
    throw promptErr;
  }

  // One bounded recovery pass gives the model a chance to check omitted sources.
  // Missing sources must not prevent a safe customer response.
  if (!options.abortSignal?.aborted && !getTerminalAssistantError(session.messages)) {
    const missingSources = getMissingCustomerEvidenceSources(options.customerMessage, [...liveExecutedTools.values()]);
    if (missingSources.length > 0) {
      console.log(`[Agent] Evidence source gaps for ticket #${options.ticketId}: ${missingSources.join(', ')}; requesting one recovery pass`);
      await session.prompt(`The following relevant read-only sources have not yielded evidence: ${missingSources.join(', ')}. Use the dify-saas-support skill and OpenConnector to check them when available. For education credits, verify current policy and official docs as well as account data before making a definitive claim. Correct malformed SQL before retrying and stop repeating the same failing query. Then return the two required sections in English. If evidence remains unavailable, explain the exact uncertainty and a customer or human-support next step; do not invent a conclusion.`);
    }
  }

  if (options.abortSignal?.aborted || (session as any).aborted) {
    const abortErr = new Error(`Agent execution aborted for ticket #${options.ticketId}`);
    abortErr.name = 'AbortError';
    throw abortErr;
  }

  const finishedAt = new Date().toISOString();
  const totalDurationMs = Date.now() - startTime;

  console.log(`\n[Agent] Prompt finished in ${totalDurationMs}ms. Total messages in session: ${session.messages.length}`);
  
  const messages = (session.messages || (session as any).state?.messages || []) as any[];
  const toolResultsMap = new Map<string, any>();
  for (const m of messages) {
    if (m.role === 'toolResult' || m.role === 'tool_result' || m.role === 'tool') {
      const callId = m.toolCallId || m.id || m.tool_call_id || m.tool_use_id;
      if (callId) {
        let contentVal = m.content;
        if (Array.isArray(contentVal)) {
          const textParts = contentVal
            .filter((p: any) => p && (p.type === 'text' || typeof p.text === 'string'))
            .map((p: any) => p.text);
          if (textParts.length > 0) {
            const joined = textParts.join('\n');
            try {
              contentVal = JSON.parse(joined);
            } catch {
              contentVal = joined;
            }
          }
        } else if (typeof contentVal === 'string') {
          try {
            contentVal = JSON.parse(contentVal);
          } catch {
            // keep as string
          }
        }
        toolResultsMap.set(callId, m.isError ? { isError: true, details: contentVal } : contentVal);
      }
    } else if (Array.isArray(m.content)) {
      for (const part of m.content) {
        if (part && (part.type === 'tool_result' || part.type === 'toolResult')) {
          const callId = part.toolUseId || part.tool_use_id || part.toolCallId || part.tool_call_id;
          if (callId) {
            toolResultsMap.set(callId, part.content ?? part.output ?? null);
          }
        }
      }
    }
  }

  const steps: AgentTraceStep[] = [];
  const thinkingList: string[] = [];
  const toolCallsList: any[] = [];
  let finalAnswer = '';

  for (const m of messages) {
    if (m.role === 'assistant' && Array.isArray(m.content)) {
      for (const part of m.content) {
        if (part.type === 'thinking' && (part.thinking || part.text)) {
          const tText = part.thinking || part.text;
          thinkingList.push(tText);
          steps.push({
            type: 'thinking',
            thinking: tText,
          });
        } else if (part.type === 'toolCall' || part.type === 'tool_call' || part.type === 'tool_use' || part.type === 'toolUse') {
          const toolCallId = part.id || part.toolCallId || part.call_id || part.tool_use_id;
          const toolName = part.name || part.toolName;
          const res = toolResultsMap.get(toolCallId) ?? liveExecutedTools.get(toolCallId)?.result ?? null;
          const args = part.arguments || part.args || part.input || liveExecutedTools.get(toolCallId)?.arguments || {};
          const status = liveExecutedTools.get(toolCallId)?.status ?? 'succeeded';
          const toolItem = {
            toolCallId: toolCallId,
            toolName: toolName,
            service: inferToolService(toolName, args),
            arguments: args,
            result: res,
            status: status,
          };
          toolCallsList.push(toolItem);
          steps.push({
            type: 'tool_call',
            ...toolItem,
          });
        } else if (part.type === 'text' && part.text) {
          finalAnswer = part.text;
          steps.push({
            type: 'answer',
            text: part.text,
          });
        }
      }
    } else if (m.role === 'assistant' && typeof m.content === 'string') {
      finalAnswer = m.content;
      steps.push({
        type: 'answer',
        text: m.content,
      });
    }
  }

  const mergedCalls = mergeToolCalls(toolCallsList, liveExecutedTools.values());
  toolCallsList.splice(0, toolCallsList.length, ...mergedCalls);
  const remainingCalls = new Map(mergedCalls.map(call => [call.toolCallId, call]));
  for (const step of steps) {
    if (step.type !== 'tool_call' || !step.toolCallId) continue;
    const call = remainingCalls.get(step.toolCallId);
    if (call) { Object.assign(step, call); remainingCalls.delete(step.toolCallId); }
  }
  for (const call of remainingCalls.values()) steps.push({ type: 'tool_call', ...call });

  if (!finalAnswer.trim()) {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === 'assistant' && msg.content) {
        if (typeof msg.content === 'string') {
          finalAnswer = msg.content;
          break;
        } else if (Array.isArray(msg.content)) {
          const textParts = msg.content
            .filter((part: any) => part.type === 'text' && part.text)
            .map((part: any) => part.text);
          if (textParts.length > 0) {
            finalAnswer = textParts.join('\n');
            break;
          }
        }
      }
    }
  }

  const trace: AgentTracePayload = {
    ticketId: options.ticketId,
    requesterEmail: options.requesterEmail,
    customerMessage: options.customerMessage,
    model: modelName,
    startedAt,
    finishedAt,
    totalDurationMs,
    rounds: completedRounds,
    tokens: sumRoundTokens(completedRounds),
    thinking: thinkingList,
    toolCalls: toolCallsList,
    steps,
    finalReply: '',
  };
  // Keep the complete tool timeline even when model output or delivery
  // validation fails. The Worker records this trace as a failed run.
  options.onTrace?.(trace);

  const modelError = getTerminalAssistantError(messages);
  if (modelError) {
    throw new Error(`Pi Agent model request failed for ticket #${options.ticketId}: ${modelError}`);
  }
  if (!finalAnswer.trim()) {
    throw new Error(`Pi Agent finished but produced no text response for ticket #${options.ticketId}`);
  }

  // Extract dual-track outputs: customer reply + internal engineering triage note
  const { customerReply, internalNote } = extractDualTrackOutputs(finalAnswer);
  const publicReplyAllowed = options.publicReplyAllowed !== false;
  // An internal Zendesk draft is still customer-facing prose for human review.
  // Keep language and separation checks, but never drop a ticket solely because
  // a suggested source was unavailable. Do not deliver the model's unsupported
  // claims in that case: use a deterministic uncertainty reply instead.
  const missingSources = getMissingCustomerEvidenceSources(options.customerMessage, toolCallsList);
  const safeCustomerReply = missingSources.length > 0
    ? buildEvidenceGapReply(missingSources)
    : customerReply.trim();
  validateCustomerDelivery(finalAnswer, safeCustomerReply);

  // Unified English footer for customer reply
  const footer = '\n\n---\nThis reply was sent by Dify AI Agent. If this does not resolve your issue, reply “human”. A human support engineer will respond as soon as possible during support hours (New York time): Sunday–Thursday nights, 9:00 PM–6:00 AM EDT (daylight saving time) or 8:00 PM–5:00 AM EST (standard time).';
  const fullReply = publicReplyAllowed ? safeCustomerReply + footer : '';
  trace.finalReply = fullReply;

  return {
    reply: fullReply,
    customerReply: safeCustomerReply,
    internalNote: (internalNote || (!publicReplyAllowed ? finalAnswer : ''))
      ? `🤖 [Dify AI Agent 调查备忘录]\n\n${internalNote || finalAnswer}` : undefined,
    trace,
  };
}
