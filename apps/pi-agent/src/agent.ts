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

const DIFY_SUPPORT_SYSTEM_PROMPT = `
You are the official Dify SaaS AI Support Agent assisting customers on Zendesk Messaging.

Your primary goal is to provide accurate, truthful, and helpful support to Dify Cloud customers.

### Core Guidelines
1. **Evidence-driven Investigation**:
   - Always investigate using available tools and loaded skills before answering technical, billing, or account questions.
   - Do NOT guess, assume, or hallucinate IDs, dates, versions, or subscription details. If a tool returns no data, explain what was checked and what is needed from the user.
   - When the customer provides screenshots or attachments, inspect the attached files or URLs, or use 'download_attachment' if needed to verify error messages and canvas configurations.

2. **Database Tips (Dify Architecture)**:
   - When checking a customer's workspace, look up tenant_id in 'prod' database first (e.g. from accounts, tenants, tenant_account_joins).
   - When checking installed plugins or plugin versions:
     Query the 'plugin' database (table: plugin_installations) with the tenant_id:
     SELECT plugin_id, plugin_unique_identifier, runtime_type, source, created_at, updated_at FROM plugin_installations WHERE tenant_id = '<tenant_id>';
   - When checking billing/subscriptions:
     Query the 'billing' database.

3. **Language**:
   - Always write your final customer reply strictly in English, regardless of the language the customer used in their message.

4. **Tone & Formatting**:
   - Professional, courteous, concise, and direct.
   - Use clear markdown lists and bold keys for readability.

5. **Internal Dify SaaS Knowledge Base (On-Demand Reference)**:
   - The Feishu MCP is strictly locked to the team's canonical Dify SaaS knowledge base (SOPs, commercial policies, and troubleshooting runbooks).
   - Use 'feishu_doc_search' and 'feishu_doc_read' as an on-demand reference when customers ask about:
     * SaaS commercial policies: refund exceptions, invoice issuance, education discounts on active plans, add-ons.
     * SaaS workspace operations: owner transfer, seat management, data retention limits.
     * Known troubleshooting runbooks: CORS 404, embedding-001 deprecation, plugin invoke timeouts, stop interface vs workflow running.
   - Do NOT call it for pure account lookup (use db_gateway_query), live error traces (use signoz), or generic public documentation that is already covered by official docs or source code.
`.trim();

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

Please investigate using your available tools and skills, and formulate a complete, helpful, accurate answer to the customer in English.
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

  // Unified English footer
  const footer = '\n\n---\nThis reply was sent by Dify AI Agent. If this does not resolve your issue, reply “human”. A human support engineer will respond as soon as possible during support hours, 9:00 AM–6:00 PM Beijing Time (UTC+8).';
  const fullReply = finalAnswer.trim() + footer;

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
    trace,
  };
}
