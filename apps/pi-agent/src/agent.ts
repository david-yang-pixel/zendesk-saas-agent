import {
  ModelRuntime,
  createAgentSession,
  SessionManager,
} from '@earendil-works/pi-coding-agent';
import { CustomTool } from './mcp.js';

export interface AgentRunOptions {
  ticketId: number;
  customerMessage: string;
  requesterEmail?: string;
  tools: CustomTool[];
  modelName?: string;
  modelBaseUrl?: string;
  apiKey?: string;
}

const DIFY_SUPPORT_SYSTEM_PROMPT = `
You are the official Dify SaaS AI Support Agent assisting customers on Zendesk Messaging.

Your primary goal is to provide accurate, truthful, and helpful support to Dify Cloud customers.

### Core Guidelines
1. **Evidence-driven Investigation**:
   - Always investigate using available tools before answering technical, billing, or account questions.
   - Do NOT guess, assume, or hallucinate IDs, dates, versions, or subscription details. If a tool returns no data, explain what was checked and what is needed from the user.

2. **Database Tips (Dify Architecture)**:
   - When checking a customer's workspace, look up tenant_id in 'prod' database first (e.g. from accounts, tenants, tenant_account_joins).
   - When checking installed plugins or plugin versions:
     Query the 'plugin' database (table: plugin_installations) with the tenant_id:
     SELECT plugin_id, plugin_unique_identifier, runtime_type, source, created_at, updated_at FROM plugin_installations WHERE tenant_id = '<tenant_id>';
   - When checking billing/subscriptions:
     Query the 'billing' database.

3. **Language Matching**:
   - Always respond in the exact same language used by the customer in their message (e.g., Simplified Chinese, Traditional Chinese, English, Japanese, etc.).

4. **Tone & Formatting**:
   - Professional, courteous, concise, and direct.
   - Use clear markdown lists and bold keys for readability.
`.trim();

export async function runPiSupportAgent(options: AgentRunOptions): Promise<string> {
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
        input: ['text'],
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

  const { session } = await createAgentSession({
    sessionManager: SessionManager.inMemory(),
    modelRuntime: runtime,
    model: model,
    customTools: options.tools,
    tools: options.tools.map(t => t.name),
  });

  session.subscribe((event: any) => {
    if (event.type === 'message_update') {
      const sub = event.assistantMessageEvent;
      if (sub?.type === 'text_delta') {
        process.stdout.write(sub.delta);
      } else if (sub?.type === 'tool_call') {
        console.log(`\n[Agent Tool Call] ${sub.name}:`, JSON.stringify(sub.arguments).slice(0, 150));
      }
    } else if (event.type === 'tool_result') {
      console.log(`\n[Agent Tool Result] ${event.toolName}:`, JSON.stringify(event.result).slice(0, 150));
    } else if (event.type === 'error') {
      console.error('\n[Agent Error Event]', event.error);
    } else {
      console.log(`\n[Agent Event] ${event.type}`);
    }
  });

  const prompt = `
Customer Ticket #${options.ticketId}
Requester Email: ${options.requesterEmail || 'Unknown'}
Customer Message:
"""
${options.customerMessage}
"""

System Instructions:
${DIFY_SUPPORT_SYSTEM_PROMPT}

Please investigate using your available tools and formulate a complete, helpful, accurate answer to the customer.
`.trim();

  console.log(`[Agent] Sending prompt to session for ticket #${options.ticketId}...`);
  await session.prompt(prompt);

  console.log(`\n[Agent] Prompt finished. Total messages in session: ${session.messages.length}`);
  for (const m of session.messages as any[]) {
    const preview = typeof m?.content === 'string' ? m.content : JSON.stringify(m?.content ?? '');
    console.log(`[Agent Msg] role=${m?.role}:`, (preview || '').slice(0, 100));
  }

  // Extract final assistant message from session.messages
  const messages = session.messages as any[];
  let finalAnswer = '';
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

  if (!finalAnswer.trim()) {
    throw new Error(`Pi Agent finished but produced no text response for ticket #${options.ticketId}`);
  }

  // Append localized footer
  const isChinese = /[\u4e00-\u9fa5]/.test(options.customerMessage) || /[\u4e00-\u9fa5]/.test(finalAnswer);
  const footer = isChinese
    ? '\n\n---\n此回复由 Dify AI Agent 发送。如果问题仍未解决，请回复“human”。人工支持工程师将在北京时间(UTC+8)9:00-18:00 的服务时间内尽快回复。'
    : '\n\n---\nThis reply was sent by Dify AI Agent. If the issue persists, reply "human". Human support engineers will respond as soon as possible during business hours (UTC+8 9:00-18:00).';

  return finalAnswer.trim() + footer;
}
