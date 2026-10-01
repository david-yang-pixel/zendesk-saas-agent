import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, it, vi } from 'vitest';
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { discoverMcpTools } from './mcp.js';
import { ModelFileInputs } from './model-files.js';

it('puts original PDF bytes in the HTTP body through the real MCP, Pi and Responses SDK boundaries', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-native-file-test-'));
  const base64 = Buffer.from('%PDF-1.4\nOffline file transport fixture').toString('base64');
  let requestBody: any;
  // Every HTTP boundary is intercepted; no model calls, customer data or live tickets.
  vi.stubGlobal('fetch', vi.fn(async (input: any, init?: RequestInit) => {
    const request = new Request(input, init);
    const body = await request.json();
    if (body.method === 'tools/list') return new Response(JSON.stringify({ result: { tools: [{ name: 'download_attachment' }] } }));
    requestBody = body;
    return new Response(JSON.stringify({ error: { message: 'Offline transport test stops after capturing the request', type: 'invalid_request_error' } }),
      { status: 400, headers: { 'content-type': 'application/json' } });
  }));
  let session: Awaited<ReturnType<typeof createAgentSession>>['session'] | undefined;
  const files = new ModelFileInputs();
  try {
    const [download] = await discoverMcpTools({ name: 'fixture', endpoint: 'https://mcp.example.test', apiKey: 'test',
      executeTool: async () => ({ content: [{ type: 'resource', resource: {
        uri: 'attachment://receipt.pdf', mimeType: 'application/pdf', blob: base64,
      } }] }),
    });
    const wrapped = files.wrap(download);
    const result = await wrapped.execute('call_pdf|fc_pdf', {});
    const runtime = await ModelRuntime.create({ modelsPath: null, authPath: path.join(directory, 'auth.json'), refreshOnCreate: false });
    runtime.registerProvider('openai', { baseUrl: 'https://model.example.test/v1', apiKey: 'offline-test-key', models: [{
      id: 'gpt-6-sol', name: 'gpt-6-sol', api: 'openai-responses', reasoning: false, input: ['text', 'image'],
      contextWindow: 128000, maxTokens: 1024, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    }] });
    const settings = SettingsManager.inMemory({ transport: 'sse', compaction: { enabled: false }, retry: { enabled: false, provider: { maxRetries: 0 } } });
    const loader = new DefaultResourceLoader({ cwd: directory, agentDir: directory, settingsManager: settings,
      noExtensions: true, noContextFiles: true, noSkills: true, noPromptTemplates: true, noThemes: true,
      extensionFactories: [files.extension] });
    await loader.reload();
    ({ session } = await createAgentSession({ resourceLoader: loader, settingsManager: settings,
      sessionManager: SessionManager.inMemory(directory), modelRuntime: runtime, model: runtime.getModel('openai', 'gpt-6-sol'),
      customTools: [wrapped], tools: [wrapped.name] }));
    session.agent.state.messages = [{ role: 'assistant', api: 'openai-responses', provider: 'openai', model: 'gpt-6-sol',
      content: [{ type: 'toolCall', id: 'call_pdf|fc_pdf', name: wrapped.name, arguments: {} }],
      usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
      stopReason: 'toolUse', timestamp: Date.now() },
    { role: 'toolResult', toolCallId: 'call_pdf|fc_pdf', toolName: wrapped.name, ...result, timestamp: Date.now() }];
    await session.prompt('Inspect the downloaded receipt.');
    files.assertReady();
    const output = requestBody.input.find((item: any) => item.type === 'function_call_output' && item.call_id === 'call_pdf');
    expect(output.output).toContainEqual({ type: 'input_file', filename: 'receipt.pdf', file_data: `data:application/pdf;base64,${base64}` });
    expect(JSON.stringify(session.messages)).not.toContain(base64);
  } finally {
    session?.dispose(); files.clear(); vi.unstubAllGlobals(); fs.rmSync(directory, { recursive: true, force: true });
  }
});
