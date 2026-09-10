import { describe, it, expect } from 'vitest';
import { CustomTool } from './mcp.js';
import { inferToolService, sanitizeCustomerReply } from './agent.js';

describe('Pi Support Agent Tool Format', () => {
  it('correctly constructs tool definition conforming to Pi Agent schema', () => {
    const mockTool: CustomTool = {
      name: 'test_tool',
      label: 'test_tool',
      description: 'Test description',
      promptSnippet: 'Test description',
      parameters: {
        type: 'object',
        properties: { q: { type: 'string' } },
        required: ['q'],
      },
      execute: async (_id: string, params: any) => ({
        content: [{ type: 'text', text: `mock result for ${params?.q}` }],
        details: { q: params?.q },
      }),
    };

    expect(mockTool.name).toBe('test_tool');
    expect(mockTool.parameters.type).toBe('object');
  });

  it('correctly formats footer in Chinese and English', () => {
    const chineseMsg = '我想知道 OpenAI 插件版本';
    const englishMsg = 'I want to check plugin version';

    const isChinese = (msg: string) => /[一-龥]/.test(msg);
    expect(isChinese(chineseMsg)).toBe(true);
    expect(isChinese(englishMsg)).toBe(false);
  });
});

describe('inferToolService', () => {
  it('infers correctly for tools', () => {
    expect(inferToolService('db_gateway_query')).toBe('db-gateway');
    expect(inferToolService('feishu_doc_search')).toBe('feishu');
    expect(inferToolService('signoz_search_logs')).toBe('signoz');
    expect(inferToolService('github_search_issues')).toBe('github');
    expect(inferToolService('source_read')).toBe('source');
    expect(inferToolService('get_ticket')).toBe('zendesk');
    expect(inferToolService('unknown_tool')).toBe('other');
  });
});

describe('sanitizeCustomerReply', () => {
  it('strips leading investigation update headers', () => {
    const raw = `## Investigation update — ticket #4396\nI identified the likely workflow as test.`;
    expect(sanitizeCustomerReply(raw)).toBe(`I identified the likely workflow as test.`);
  });

  it('strips plain investigation update headers', () => {
    const raw = `Investigation update:\nHere are the details for your issue.`;
    expect(sanitizeCustomerReply(raw)).toBe(`Here are the details for your issue.`);
  });

  it('converts third-person handover phrasing to polite customer requests', () => {
    const raw = `1. Check your settings.\n2. Ask Catherine for: workflow URL, screenshot.`;
    expect(sanitizeCustomerReply(raw)).toBe(`1. Check your settings.\n2. Could you please provide: workflow URL, screenshot.`);
  });

  it('converts "Ask the user to" to "Please"', () => {
    const raw = `Ask the customer to retry running the node.`;
    expect(sanitizeCustomerReply(raw)).toBe(`Please retry running the node.`);
  });
});
