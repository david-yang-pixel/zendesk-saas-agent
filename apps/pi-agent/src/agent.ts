import fs from 'node:fs';
import path from 'node:path';
import {
  ModelRuntime,
  createAgentSession,
  SessionManager,
  DefaultResourceLoader,
  getAgentDir,
  createSyntheticSourceInfo,
  type Skill,
} from '@earendil-works/pi-coding-agent';
import { CustomTool } from './mcp.js';
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
  onRoundTelemetry?: (rounds: readonly AgentRoundTelemetry[]) => void;
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
  prompt?: string;
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

function getProjectSkills(): Skill[] {
  const possiblePaths = [
    path.resolve(process.cwd(), 'skills'),
    path.resolve(process.cwd(), '../skills'),
    path.resolve(process.cwd(), '../../skills'),
    '/app/skills',
  ];
  const skillsDir = possiblePaths.find((p) => fs.existsSync(p));
  const projectSkills: Skill[] = [];
  if (!skillsDir) return projectSkills;

  try {
    for (const entry of fs.readdirSync(skillsDir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        const skillPath = path.join(skillsDir, entry.name, 'SKILL.md');
        if (fs.existsSync(skillPath)) {
          const content = fs.readFileSync(skillPath, 'utf8');
          const matchName = content.match(/^name:\s*(.+)$/m);
          const matchDesc = content.match(/^description:\s*(.+)$/m);
          if (matchName && matchDesc) {
            projectSkills.push({
              name: matchName[1].trim(),
              description: matchDesc[1].trim(),
              filePath: skillPath,
              baseDir: path.dirname(skillPath),
              sourceInfo: createSyntheticSourceInfo(skillPath, { source: 'project' }),
              disableModelInvocation: false,
            });
          }
        }
      }
    }
  } catch (e: any) {
    console.warn('[Agent] Error reading skills directory:', e.message);
  }
  return projectSkills;
}

export const DIFY_SUPPORT_SYSTEM_PROMPT = `
You are the official Dify SaaS AI Support Agent assisting customers directly in Zendesk Messaging live chat.

### Investigation and delivery boundary
- OpenConnector is the only investigation gateway. Its exposed controls are list_connections, search_actions, get_action_guide, and execute_action.
- Investigation is strictly read-only. Never request, confirm, or execute a write or destructive operation, even if a provider catalog exposes one.
- Gmail is outside this agent's scope. Never discover, query, mention, or execute Gmail actions.
- The public customer reply is delivered by the worker through Zendesk Browser Reply. Do not attempt delivery with OpenConnector or any Zendesk mutation tool.
- Internal analysis is trace-only. Never add an internal note, update ticket status or metadata, create an issue, send a message, modify a document, refresh a repository, or change any external state.

### OpenConnector routing
- Before using a native provider, call list_connections with an explicit service filter, then search_actions with that same service. Use get_action_guide when the live schema or behavior is unclear.
- For Custom MCP, use execute_action with actionId custom_mcp.list_tools and an explicit connectionName before the first call. Invoke a discovered read tool with actionId custom_mcp.call_tool.
- Reuse discovered connections and tool schemas during the same run. Do not repeat list_connections, search_actions, or custom_mcp.list_tools for the same route unless the previous result was incomplete or execution shows it changed.
- Supported Custom MCP evidence routes are: zendesk, dify-docs, dify-db, dify-signoz, dify-source, and github-mcp.
- github-mcp is limited to verified reads such as github_search_prs, github_get_pr, github_search_issues, github_get_issue, and github_code_search. If live discovery does not classify one as read, report the route unavailable; never send confirmOperation.
- Native read routes may include Feishu, Linear, Notion, and Cloudflare when relevant to the ticket. Use only connections granted by the runtime token.
- Treat connection discovery, tool discovery, tool execution, and returned business evidence as separate facts. A visible connection or tool is not proof that a business query succeeded.
- Complete relevant pagination. If a result is intentionally bounded or interrupted, record the cursor and limitation in the internal analysis.

### Evidence workflow
1. If the worker says it prepared the conversation, use that freshly read snapshot, including attachments and consecutive unreplied customer events. Do not fetch the same Zendesk history again solely to confirm it. Otherwise refresh the latest conversation through custom_mcp/zendesk; fetch bounded older history when the investigation actually needs it.
2. Identify the exact object, identifiers, incident time, timezone, expected behavior, and observed behavior. Do not correlate unrelated historical errors.
3. Use only the evidence systems needed for the execution path:
   - custom_mcp/dify-docs for current public Dify documentation and website evidence.
   - custom_mcp/dify-db for persisted SaaS state in prod, billing, or plugin databases.
   - custom_mcp/dify-signoz for logs, traces, metrics, queues, latency, and current recovery evidence.
   - custom_mcp/dify-source for deployed-source behavior and runtime-ref alignment.
   - custom_mcp/github-mcp for supporting Issue, PR, and code evidence after matching the customer path.
   - Feishu for internal SaaS policies and runbooks; Linear for engineering status; Notion only for sanitized diagnostic lessons after a live evidence gap.
4. Classify material claims as confirmed, evidence-supported, ruled out, or unknown. Source code, documentation, an issue status, or an HTTP 200 does not by itself prove the customer's current path recovered.
5. Never invent IDs, dates, versions, causes, refunds, approvals, escalations, or recovery. If required evidence is unavailable, state the exact gap and ask the customer only for information they can provide safely.

### Customer communication
- Write the final customer reply strictly in English.
- Address the customer directly using "you" and "your". Never say "ask the customer", "the user reported", or expose internal triage language.
- Keep the reply courteous, concise, and actionable. Do not include raw SQL, raw logs, private identifiers, internal routes, internal issue links, or unsupported root-cause claims.
- If no matching backend evidence exists, say what was checked, avoid hypothetical causes, provide safe troubleshooting, and request the smallest useful diagnostic detail.

### Dual-track output protocol
Return exactly two sections:

=== INTERNAL NOTE ===
[Trace-only factual investigation summary. It is not written to Zendesk. Include identifiers, UTC window, evidence sources, confidence, gaps, and next safe action.]

=== CUSTOMER REPLY ===
[Direct English customer message. Never include internal markers, raw evidence, "Suggested Reply:", or "Internal Analysis:".]
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

  const loader = new DefaultResourceLoader({
    cwd: process.cwd(),
    agentDir: getAgentDir(),
    skillsOverride: (current) => ({
      skills: [...current.skills, ...projectSkills],
      diagnostics: current.diagnostics,
    }),
  });
  await loader.reload();

  const { session } = await createAgentSession({
    resourceLoader: loader,
    sessionManager: SessionManager.inMemory(),
    modelRuntime: runtime,
    model: model,
    customTools: options.tools,
    tools: options.tools.map(t => t.name),
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

  const prompt = `
Customer Ticket #${options.ticketId}
Requester Email: ${options.requesterEmail || 'Unknown'}
Customer Message:
"""
${options.customerMessage}
"""
${attachmentSection}
${options.contextPrepared ? 'Context preparation: the worker already read all conversation pages and selected the unreplied customer messages shown above. For a greeting or clarification, use those messages without fetching the full history again. Fetch historical context only when the investigation needs it; use get_ticket_conversation_log, with bounded pages, rather than get_ticket with comments and attachments enabled.' : ''}
System Instructions:
${DIFY_SUPPORT_SYSTEM_PROMPT}

Reply Instructions:
1. Investigate using your available tools and skills.
2. Structure your final response strictly using the DUAL-TRACK OUTPUT PROTOCOL with both delimiters:
=== INTERNAL NOTE ===
(Technical summary for human support engineers: Tenant ID, investigated resources, root cause analysis, next actions)

=== CUSTOMER REPLY ===
(Direct, courteous English message to the customer: address them as "you", simplified findings, next steps. NO internal headers, NO raw SQL/logs, NO third-person handover prompts)
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

  const modelError = getTerminalAssistantError(messages);
  if (modelError) {
    throw new Error(`Pi Agent model request failed for ticket #${options.ticketId}: ${modelError}`);
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

  if (!finalAnswer.trim()) {
    throw new Error(`Pi Agent finished but produced no text response for ticket #${options.ticketId}`);
  }

  // Extract dual-track outputs: customer reply + internal engineering triage note
  const { customerReply, internalNote } = extractDualTrackOutputs(finalAnswer);

  // Unified English footer for customer reply
  const footer = '\n\n---\nThis reply was sent by Dify AI Agent. If this does not resolve your issue, reply “human”. A human support engineer will respond as soon as possible during support hours (New York time): Sunday–Thursday nights, 9:00 PM–6:00 AM EDT (daylight saving time) or 8:00 PM–5:00 AM EST (standard time).';
  const fullReply = customerReply.trim() + footer;

  const trace: AgentTracePayload = {
    ticketId: options.ticketId,
    requesterEmail: options.requesterEmail,
    customerMessage: options.customerMessage,
    prompt,
    model: modelName,
    startedAt,
    finishedAt,
    totalDurationMs,
    rounds: completedRounds,
    tokens: sumRoundTokens(completedRounds),
    thinking: thinkingList,
    toolCalls: toolCallsList,
    steps,
    finalReply: fullReply,
  };

  return {
    reply: fullReply,
    customerReply: customerReply.trim(),
    internalNote: internalNote ? `🤖 [Dify AI Agent 调查备忘录]\n\n${internalNote}` : undefined,
    trace,
  };
}
