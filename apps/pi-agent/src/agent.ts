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
  }>;
  finalReply: string;
  totalDurationMs: number;
  tokens?: {
    input?: number;
    output?: number;
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

export function inferToolService(toolName: string): string {
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

Your primary goal is to provide accurate, courteous, and helpful support to Dify Cloud customers.

### CRITICAL: Communication Persona & Public Reply Constraints
1. **You are speaking DIRECTLY to the customer in a live chat**:
   - Address the customer directly using second person ("you", "your workspace", "your workflow").
   - NEVER speak about the customer in the third person!
   - ABSOLUTELY FORBIDDEN: NEVER write "Ask the customer for...", "Ask Catherine for...", "The user reported...", or "Tell the customer...".
   - If you need information, screenshots, or workflow URLs, ask the customer directly and politely: e.g. "Could you please share your workflow URL or a screenshot of the error?"

2. **STRICTLY FORBIDDEN: Internal Memos & Ticket Header Templates**:
   - NEVER output internal investigation headers like "## Investigation update", "ticket #4396", or internal triage memos.
   - NEVER dump internal SQL queries, raw database outputs, internal API routes (like "POST /console/api/..."), or raw backend traces into your reply.
   - Speak in clear, empathetic, customer-friendly terms. Summarize what you found and give direct, easy-to-follow steps.

3. **Incident Correlation & Workflow "Internal Error" Troubleshooting**:
   - Only correlate issues that match the customer's actual incident timeframe. Do NOT dredge up unrelated historical errors from days or weeks ago (e.g. do not blame a week-old plugin timeout if the customer is reporting an initiation error today).
   - When a customer reports an immediate "Internal Server Error" upon opening, editing, or running a workflow where no workflow_runs record exists:
     * This usually indicates a failure during workflow graph compilation or model provider initialization.
     * Check recently installed or updated plugins in the workspace via 'plugin' database ('plugin_installations' ORDER BY updated_at DESC). Faulty or incompatible plugin updates (e.g. model provider plugins like Gemini, OpenAI, Claude) frequently cause immediate internal errors.
     * If suspected, advise the customer to check the workspace plugin list, try rolling back or downgrading recently updated plugins to a previous stable version, or disabling auto-update.
     * If the root cause cannot be confirmed from available data, explain politely what you checked and ask the customer for a screenshot or the specific failing node.

### Core Investigation Guidelines
1. **Evidence-driven Investigation**:
   - Always investigate using available tools and loaded skills before answering technical, billing, or account questions.
   - Do NOT guess, assume, or hallucinate IDs, dates, versions, or subscription details. If a tool returns no data, explain what was checked and what is needed from the user.
   - When the customer provides screenshots or attachments, inspect the attached files or URLs, or use 'download_attachment' if needed to verify error messages and canvas configurations.

2. **Database Tips (Dify Architecture)**:
   - When checking a customer's workspace, look up tenant_id in 'prod' database first (e.g. from accounts, tenants, tenant_account_joins).
   - When checking installed plugins or plugin versions:
     Query the 'plugin' database (table: plugin_installations) with the tenant_id:
     SELECT plugin_id, plugin_unique_identifier, runtime_type, source, created_at, updated_at FROM plugin_installations WHERE tenant_id = '<tenant_id>' ORDER BY updated_at DESC;
   - When checking billing/subscriptions & credits:
     Query the 'billing' database:
     * Note that 'tenant_subscription_settings' (plan = 'professional') is identical for BOTH standard paid subscribers AND education discount users!
     * ALWAYS cross-reference 'education_promotion_logs' with tenant_id:
       SELECT tenant_id, account_id, valid_starts_at, valid_ends_at FROM education_promotion_logs WHERE tenant_id = '<tenant_id>' AND NOW() BETWEEN valid_starts_at AND valid_ends_at;
     * If active in 'education_promotion_logs', the workspace is on an Education Promotion: it receives a ONE-TIME grant of 200 credits with NO monthly reset/renewal. Once used up, users must configure BYOK (Bring Your Own Key). Never claim that education credits reset next month!
     * 'education_identities' confirms account-level qualification (account_id), but 'education_promotion_logs' is the sole source of truth for workspace-level application.

3. **Language**:
   - Always write your final customer reply strictly in English, regardless of the language the customer used in their message.

4. **Tone & Formatting**:
   - Professional, courteous, concise, and direct.
   - Use clear markdown lists and bold keys for readability.

5. **Knowledge Base Two-Tier Routing Matrix (P0 - STRICT ROUTING)**:
   - **Tier A: Public Product Documentation ('official_docs_search' / 'official_docs_read')**:
     * ROUTE HERE for: Product features, limits, DSL syntax, workflow node configurations (LLM, Code, IF/ELSE, Iteration), model provider setup, and API specifications.
     * DO NOT query Feishu for public product features or standard node usage.
   - **Tier B: Internal Dify SaaS Knowledge Base ('feishu_doc_search' / 'feishu_doc_read')**:
     * ROUTE HERE for: SaaS commercial policies (refund exceptions, invoice issuance, education promotion claim SOP, add-on credits), workspace governance (seat allocation, ownership transfer), and known incident runbooks (CORS 404, embedding-001 deprecation).
     * DO NOT query official docs for internal billing/refund SOPs or SaaS operational rules.

6. **Evidence Confidence & Anti-Hallucination Gate (P1 - STRICT VERIFICATION)**:
   - **Tier 1 (High Confidence - Verified Evidence)**:
     * When SigNoz traces, plugin installation timestamps, or database records confirm the exact root cause within the incident timeframe: provide direct, authoritative resolution steps (e.g. rollback plugin version, configure BYOK).
   - **Tier 2 (Inconclusive - No Backend Traces / Potential Client-side Issue)**:
     * If backend logs, traces, and database queries show NO errors around the incident time (e.g. no workflow_runs created, blank canvas, or UI action failure):
     * STRICTLY FORBIDDEN: NEVER invent hypothetical backend causes.
     * State clearly that backend services recorded no execution errors for the specified timeframe.
     * Guide the customer through client-side troubleshooting (browser extensions, hard refresh, cache) and politely request specific diagnostic details (browser F12 Console screenshot, failing node name, or request payload).
     * In the internal note, flag as '[Inconclusive - Needs Human Investigation]'.

7. **DUAL-TRACK OUTPUT PROTOCOL (MANDATORY)**:
   To ensure complete separation between technical engineering details and customer-facing chat messages, your final assistant message MUST be structured into two sections delimited by exact markers:

   === INTERNAL NOTE ===
   [Factual engineering triage note for human support engineers]
   - **Workspace / Tenant**: <tenant_id>
   - **Investigated Resources**: <apps / workflows / plugins inspected>
   - **Root Cause Analysis**: <factual finding or 'No execution trace found around reported time'>
   - **Technical Recommendations**: <concrete actions for human engineers, e.g. check plugin downgrade, database fix, or escalation>

   === CUSTOMER REPLY ===
   [Direct, empathetic message to the customer in English]
   - Greet the customer and speak directly using "you" / "your workflow".
   - Give the simplified, actionable conclusion or kindly request missing info.
   - NO internal SQL, NO raw logs, NO internal memos, and NO third-person handover prompts.
`.trim();

export function sanitizeCustomerReply(rawReply: string): string {
  if (!rawReply) return '';
  let text = rawReply.trim();

  // Strip leading internal investigation headers like "## Investigation update — ticket #4396" or "Investigation update:"
  text = text.replace(/^#*\s*Investigation\s+update(?::|\s*[-—:]\s*(?:ticket\s*#?\d+)?|\s*[-—:])?\s*\n+/i, '');
  text = text.replace(/^#*\s*Ticket\s*#?\d+\s*Investigation(?:\s+Update)?(?::|\s*[-—:])?\s*\n+/i, '');

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
        reasoning: true,
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

  session.subscribe((event: any) => {
    if (event.type === 'tool_execution_start') {
      const callId = event.toolCallId || `call-${liveExecutedTools.size + 1}`;
      console.log(`\n[Agent Tool Call Start] ${event.toolName} (${callId}):`, JSON.stringify(event.args).slice(0, 200));
      liveExecutedTools.set(callId, {
        toolCallId: callId,
        toolName: event.toolName,
        service: inferToolService(event.toolName),
        arguments: event.args ?? {},
        result: null,
        status: 'running',
      });
    } else if (event.type === 'tool_execution_end') {
      const callId = event.toolCallId || `call-${liveExecutedTools.size}`;
      console.log(`\n[Agent Tool Result End] ${event.toolName} (${callId}): isError=${event.isError}, result:`, JSON.stringify(event.result).slice(0, 200));
      const existing = liveExecutedTools.get(callId);
      if (existing) {
        existing.result = event.result;
        existing.status = event.isError ? 'failed' : 'succeeded';
      } else {
        liveExecutedTools.set(callId, {
          toolCallId: callId,
          toolName: event.toolName,
          service: inferToolService(event.toolName),
          arguments: event.args ?? {},
          result: event.result,
          status: event.isError ? 'failed' : 'succeeded',
        });
      }
    } else if (event.type === 'message_update') {
      const sub = event.assistantMessageEvent;
      if (sub?.type === 'text_delta') {
        process.stdout.write(sub.delta);
      } else if (sub?.type === 'toolCall' || sub?.type === 'tool_call') {
        console.log(`\n[Agent Tool Call Update] ${sub.name}:`, JSON.stringify(sub.arguments).slice(0, 150));
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
        toolResultsMap.set(callId, contentVal);
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
            service: inferToolService(toolName),
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

  // Fallback: if session messages didn't yield tool calls, populate from liveExecutedTools
  if (toolCallsList.length === 0 && liveExecutedTools.size > 0) {
    console.log(`[Agent] Populating ${liveExecutedTools.size} tool calls from live event listener fallback`);
    for (const item of liveExecutedTools.values()) {
      toolCallsList.push(item);
      steps.push({
        type: 'tool_call',
        ...item,
      });
    }
  }

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
  const footer = '\n\n---\nThis reply was sent by Dify AI Agent. If this does not resolve your issue, reply “human”. A human support engineer will respond as soon as possible during support hours, 9:00 AM–6:00 PM Beijing Time (UTC+8).';
  const fullReply = customerReply.trim() + footer;

  const trace: AgentTracePayload = {
    ticketId: options.ticketId,
    requesterEmail: options.requesterEmail,
    customerMessage: options.customerMessage,
    model: modelName,
    startedAt,
    finishedAt,
    totalDurationMs,
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
