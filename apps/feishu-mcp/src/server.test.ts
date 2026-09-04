import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { AddressInfo } from 'node:net';
import { validateReadOnlyCommand } from './lark.js';
import { getToolDefinitions, executeTool } from './tools.js';
import { createServer } from './server.js';

describe('Feishu MCP Read-Only Security Guard', () => {
  it('allows safe read-only commands', () => {
    expect(() => validateReadOnlyCommand(['docs', '+fetch', '--doc', 'test_doc'])).not.toThrow();
    expect(() => validateReadOnlyCommand(['wiki', '+space-list', '--as', 'bot', '--json'])).not.toThrow();
    expect(() => validateReadOnlyCommand(['wiki', '+node-list', '--space-id', 'sp_123', '--as', 'bot'])).not.toThrow();
    expect(() => validateReadOnlyCommand(['wiki', '+node-get', '--node-token', 'nd_123', '--as', 'bot'])).not.toThrow();
    expect(() => validateReadOnlyCommand(['docs', '+search', '--query', 'incident', '--json'])).not.toThrow();
    expect(() => validateReadOnlyCommand(['whoami'])).not.toThrow();
  });

  it('strictly blocks write and mutation commands', () => {
    expect(() => validateReadOnlyCommand(['docs', '+create'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['docs', '+update'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['wiki', '+node-create'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['wiki', '+node-delete'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['wiki', '+space-create'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['wiki', '+delete-space'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['wiki', '+move'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['wiki', '+member-add'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['wiki', '+member-remove'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['drive', '+upload'])).toThrow(/Forbidden argument|Command not permitted/);
    expect(() => validateReadOnlyCommand(['docs', '+media-insert'])).toThrow(/Forbidden argument|Command not permitted/);
  });

  it('strictly blocks dangerous write flags', () => {
    expect(() => validateReadOnlyCommand(['docs', '+fetch', '--doc', '123', '--yes'])).toThrow(/Forbidden argument/);
    expect(() => validateReadOnlyCommand(['docs', '+fetch', '--doc', '123', '--force'])).toThrow(/Forbidden argument/);
    expect(() => validateReadOnlyCommand(['docs', '+fetch', '--doc', '123', '--overwrite'])).toThrow(/Forbidden argument/);
  });
});

describe('Feishu MCP Tool Registry', () => {
  it('exposes exactly 5 read-only tools', () => {
    const tools = getToolDefinitions();
    expect(tools.length).toBe(5);
    const names = tools.map((t) => t.name);
    expect(names).toContain('feishu_doc_read');
    expect(names).toContain('feishu_wiki_list_spaces');
    expect(names).toContain('feishu_wiki_list_nodes');
    expect(names).toContain('feishu_wiki_get_node');
    expect(names).toContain('feishu_doc_search');

    for (const tool of tools) {
      expect(tool.name.startsWith('feishu_')).toBe(true);
      expect(tool.description).toBeTruthy();
      expect(tool.inputSchema.type).toBe('object');
    }
  });

  it('rejects unlisted tools in executeTool', async () => {
    await expect(executeTool('feishu_doc_create', {})).rejects.toThrow(/not allowed in read-only mode/);
    await expect(executeTool('delete_ticket', {})).rejects.toThrow(/not allowed in read-only mode/);
  });

  it('validates required arguments for tools', async () => {
    const emptyDoc = await executeTool('feishu_doc_read', {});
    expect(emptyDoc.isError).toBe(true);
    expect(emptyDoc.content[0].text).toContain('doc_token is required');

    const emptyNode = await executeTool('feishu_wiki_get_node', {});
    expect(emptyNode.isError).toBe(true);
    expect(emptyNode.content[0].text).toContain('node_token is required');

    const emptyNodes = await executeTool('feishu_wiki_list_nodes', {});
    expect(emptyNodes.isError).toBe(true);
    expect(emptyNodes.content[0].text).toContain('space_id is required');

    const emptySearch = await executeTool('feishu_doc_search', {});
    expect(emptySearch.isError).toBe(true);
    expect(emptySearch.content[0].text).toContain('query is required');
  });
});

describe('Feishu MCP HTTP Server', () => {
  let server: ReturnType<typeof createServer>;
  let baseUrl: string;

  beforeAll(async () => {
    process.env.FEISHU_MCP_API_KEY = 'test-secret-key';
    server = createServer();
    await new Promise<void>((resolve) => {
      server.listen(0, '127.0.0.1', () => {
        const addr = server.address() as AddressInfo;
        baseUrl = `http://127.0.0.1:${addr.port}`;
        resolve();
      });
    });
  });

  afterAll(async () => {
    delete process.env.FEISHU_MCP_API_KEY;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it('responds to healthcheck /readyz without auth', async () => {
    const res = await fetch(`${baseUrl}/readyz`);
    expect(res.status).toBe(200);
    const body = await res.json() as any;
    expect(body.status).toBe('ready');
    expect(body.service).toBe('feishu-mcp');
    expect(body.tools).toBe(5);
  });

  it('rejects /mcp requests without valid Bearer token', async () => {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    });
    expect(res.status).toBe(401);
  });

  it('serves tools/list over /mcp with Bearer token', async () => {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer test-secret-key',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 42, method: 'tools/list' }),
    });
    expect(res.status).toBe(200);
    const body = await res.json() as any;
    expect(body.jsonrpc).toBe('2.0');
    expect(body.id).toBe(42);
    expect(Array.isArray(body.result.tools)).toBe(true);
    expect(body.result.tools.length).toBe(5);
  });

  it('returns JSON-RPC error for unknown methods', async () => {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer test-secret-key',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 99, method: 'unknown/method' }),
    });
    expect(res.status).toBe(404);
    const body = await res.json() as any;
    expect(body.error.code).toBe(-32601);
  });
});
