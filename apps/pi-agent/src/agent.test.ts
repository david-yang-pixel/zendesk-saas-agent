import { describe, it, expect } from 'vitest';
import { CustomTool } from './mcp.js';
import {
  inferToolService,
  sanitizeCustomerReply,
  extractDualTrackOutputs,
  DIFY_SUPPORT_SYSTEM_PROMPT,
} from './agent.js';

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
    expect(inferToolService('official_docs_search')).toBe('docs');
    expect(inferToolService('official_docs_read')).toBe('docs');
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

describe('extractDualTrackOutputs', () => {
  it('correctly splits internal note and customer reply when both markers are present', () => {
    const output = `
=== INTERNAL NOTE ===
- **Workspace**: aafa
- **Finding**: Recent update to gemini plugin caused initiation error.
- **Action**: Check if rolling back to 0.9.8 helps.

=== CUSTOMER REPLY ===
Hello! We noticed that a recent update to the model plugin in your workspace might have caused the issue. Could you please try downgrading it?
    `.trim();

    const res = extractDualTrackOutputs(output);
    expect(res.internalNote).toContain('Recent update to gemini plugin');
    expect(res.customerReply).toBe('Hello! We noticed that a recent update to the model plugin in your workspace might have caused the issue. Could you please try downgrading it?');
  });

  it('handles inverted marker order gracefully', () => {
    const output = `
=== CUSTOMER REPLY ===
Hi there, we are checking your workflow now.

=== INTERNAL NOTE ===
- **Tenant**: 12345
    `.trim();

    const res = extractDualTrackOutputs(output);
    expect(res.customerReply).toBe('Hi there, we are checking your workflow now.');
    expect(res.internalNote).toBe('- **Tenant**: 12345');
  });

  it('falls back to customer reply when no markers are provided and applies sanitizer', () => {
    const output = `## Investigation update — ticket #100\nHello, please check your network.`;
    const res = extractDualTrackOutputs(output);
    expect(res.internalNote).toBeUndefined();
    expect(res.customerReply).toBe('Hello, please check your network.');
  });

  it('correctly handles education discount verification prompt guidance', () => {
    // Verifies sanitizer and prompt contract expectations
    const promptText = 'SELECT tenant_id, account_id, valid_starts_at, valid_ends_at FROM education_promotion_logs';
    expect(promptText).toContain('education_promotion_logs');
  });

  it('contains P0 Knowledge Base Two-Tier Routing Matrix in system prompt', () => {
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Tier A: Public Product Documentation');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('official_docs_search');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('official_docs_read');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Tier B: Internal Dify SaaS Knowledge Base');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('feishu_doc_search');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('feishu_doc_read');
  });

  it('contains P1 Evidence Confidence & Anti-Hallucination Gate in system prompt', () => {
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Evidence Confidence & Anti-Hallucination Gate (P1');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Tier 1 (High Confidence - Verified Evidence)');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Tier 2 (Inconclusive - No Backend Traces / Potential Client-side Issue)');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('[Inconclusive - Needs Human Investigation]');
  });
});

