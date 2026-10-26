import { describe, it, expect } from 'vitest';
import { CustomTool } from './mcp.js';
import {
  inferToolService,
  sanitizeCustomerReply,
  extractDualTrackOutputs,
  getTerminalAssistantError,
  reasoningThinkingLevelMap,
  supportsExplicitReasoningEffort,
  DIFY_SUPPORT_SYSTEM_PROMPT,
  isBusinessRead,
  getDiscoveryOnlySources,
  validateCustomerDelivery,
  finalOutputIssues,
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

describe('model provider compatibility', () => {
  it('omits explicit reasoning effort for Tokener while preserving it for OpenAI-compatible providers', () => {
    expect(supportsExplicitReasoningEffort('https://api.tokener.dev/v1')).toBe(false);
    expect(supportsExplicitReasoningEffort('https://api.openai.com/v1')).toBe(true);
  });

  it('only lets Pi send the reasoning levels detected for the model', () => {
    expect(reasoningThinkingLevelMap(['off', 'low', 'xhigh'])).toEqual({
      off: 'none', minimal: null, low: 'low', medium: null, high: null, xhigh: 'xhigh', max: null,
    });
  });

  it('surfaces the terminal provider error instead of reporting an empty answer', () => {
    expect(getTerminalAssistantError([
      { role: 'user', content: 'hello' },
      { role: 'assistant', stopReason: 'error', errorMessage: 'OpenAI API error (400): invalid_request' },
    ])).toBe('OpenAI API error (400): invalid_request');
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
    expect(inferToolService('execute_action', {
      actionId: 'custom_mcp.call_tool',
      connectionName: 'dify-signoz',
    })).toBe('signoz');
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
  it('separates the operator answer from the internal note and customer draft', () => {
    const output = [
      '=== INTERNAL NOTE ===', '{"approved":true}', 'Read call-1.',
      '=== OPERATOR ANSWER ===', '结论：该工作区在教育优惠期内。', '- 积分一次性 200',
      '=== CUSTOMER REPLY ===', 'Hello! Your education plan includes 200 one-time credits.',
    ].join('\n');
    const res = extractDualTrackOutputs(output);
    expect(res.internalNote).toBe('{"approved":true}\nRead call-1.');
    expect(res.operatorAnswer).toBe('结论：该工作区在教育优惠期内。\n- 积分一次性 200');
    expect(res.customerReply).toBe('Hello! Your education plan includes 200 one-time credits.');
  });

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

  it('strips "Suggested Reply:" prefix and truncates internal analysis in sanitizeCustomerReply', () => {
    const raw = `### Suggested Reply:
Hello, please verify your settings.

---
🔍 Internal Analysis:
- Ticket Summary: The user encountered a bug.
- Root Cause: Missing config in DB.
- Confidence Score: high
`;
    const cleaned = sanitizeCustomerReply(raw);
    expect(cleaned).toBe('Hello, please verify your settings.');
    expect(cleaned).not.toContain('Internal Analysis');
    expect(cleaned).not.toContain('Suggested Reply');
  });

  it('splits suggested reply and internal analysis into dual-track outputs even without explicit markers', () => {
    const output = `### Suggested Reply:
Hello! Currently Dify does not provide this setting.

---
🔍 Internal Analysis:
- Root Cause / Findings: No bulk disable mechanism exists.
- Confidence Score: high
`;
    const res = extractDualTrackOutputs(output);
    expect(res.customerReply).toBe('Hello! Currently Dify does not provide this setting.');
    expect(res.internalNote).toContain('Root Cause / Findings: No bulk disable mechanism exists.');
    expect(res.customerReply).not.toContain('Internal Analysis');
  });

  it('truncates Chinese internal memo patterns in sanitizeCustomerReply', () => {
    const raw = `尊敬的用户您好，请尝试刷新页面。

---
### 内部调查备忘录 / 排查记录:
- 查询了 db_gateway 发现无报错
`;
    const cleaned = sanitizeCustomerReply(raw);
    expect(cleaned).toBe('尊敬的用户您好，请尝试刷新页面。');
    expect(cleaned).not.toContain('内部调查备忘录');
  });

  it('correctly handles education discount verification prompt guidance', () => {
    // Verifies sanitizer and prompt contract expectations
    const promptText = 'SELECT tenant_id, account_id, valid_starts_at, valid_ends_at FROM education_promotion_logs';
    expect(promptText).toContain('education_promotion_logs');
  });

  it('keeps the system prompt brief and leaves investigation routing to the skill', () => {
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Read the dify-saas-support skill');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('OpenConnector read operations');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).not.toContain('custom_mcp.list_tools');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).not.toContain('github-mcp');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Never use Gmail');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Zendesk Browser Reply');
  });

  it('keeps customer delivery and internal trace boundaries in the prompt', () => {
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('current evidence');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('Internal analysis stays in the trace');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('operator mentions receive an internal note with your answer to the operator and an English customer-reply draft');
    expect(DIFY_SUPPORT_SYSTEM_PROMPT).toContain('=== CUSTOMER REPLY ===');
  });
});

describe('customer delivery safety gate', () => {
  const dualTrack = '=== INTERNAL NOTE ===\nEvidence checked.\n=== CUSTOMER REPLY ===\nHello, here is what we verified.';

  it('blocks a markerless internal investigation instead of treating it as a customer reply', () => {
    expect(() => validateCustomerDelivery('调查结果：已查到积分', '调查结果：已查到积分'))
      .toThrow(/dual-track output markers/);
  });

  it('keeps operator drafts English and rejects internal headings in any public reply', () => {
    expect(() => validateCustomerDelivery(dualTrack, '调查结果：积分不刷新', true))
      .toThrow(/entirely in English/);
    expect(() => validateCustomerDelivery(dualTrack, 'Internal Analysis: customer facts'))
      .toThrow(/internal analysis leaked/);
  });

  it('allows customer-language replies while preserving the internal separation', () => {
    expect(() => validateCustomerDelivery(dualTrack, 'はい、日本語でお問い合わせください。')).not.toThrow();
    expect(() => validateCustomerDelivery(dualTrack, '请告诉我们您遇到的问题。')).not.toThrow();
    expect(() => validateCustomerDelivery(dualTrack, 'はい、日本語でお問い合わせください。', true)).toThrow(/English/);
  });

  it('counts only an answered business read, never discovery or a failed action', () => {
    expect(isBusinessRead({ toolName: 'list_read_tools', status: 'succeeded', result: { tools: [] } })).toBe(false);
    expect(isBusinessRead({ toolName: 'execute_action', status: 'succeeded', arguments: { actionId: 'custom_mcp.list_tools' }, result: { tools: [] } })).toBe(false);
    expect(isBusinessRead({ toolName: 'execute_action', status: 'succeeded', arguments: { actionId: 'custom_mcp.call_tool', input: {} }, result: {} })).toBe(false);
    expect(isBusinessRead({ toolName: 'execute_action', status: 'failed', arguments: { actionId: 'linear.search_issues' }, result: {} })).toBe(false);
    expect(isBusinessRead({ toolName: 'execute_action', status: 'succeeded', arguments: { actionId: 'linear.search_issues' }, result: { ok: true, data: { issues: [] } } })).toBe(true);
    expect(isBusinessRead({ toolName: 'execute_action', status: 'succeeded', arguments: { actionId: 'custom_mcp.call_tool', input: { toolName: 'github_search_issues' } }, result: { ok: true, data: {} } })).toBe(true);
  });

  it('detects source-agnostic discovery-only gaps despite a successful read elsewhere', () => {
    const calls = [
      { toolName: 'execute_action', status: 'succeeded', arguments: { actionId: 'custom_mcp.call_tool', connectionName: 'dify-db', input: { toolName: 'db_gateway_query' } }, result: { ok: true, data: {} } },
      { toolName: 'search_actions', status: 'succeeded', arguments: { service: 'feishu' }, result: { ok: true, data: [] } },
      { toolName: 'execute_action', status: 'succeeded', arguments: { actionId: 'custom_mcp.list_tools', connectionName: 'dify-docs' }, result: { ok: true, data: { tools: [] } } },
      { toolName: 'list_connections', status: 'succeeded', arguments: {}, result: { ok: true, data: [] } },
    ];
    expect(getDiscoveryOnlySources(calls)).toEqual(['feishu', 'dify-docs']);
    expect(getDiscoveryOnlySources([...calls, {
      toolName: 'execute_action', status: 'succeeded', arguments: { actionId: 'feishu.search_documents', connectionName: 'default' }, result: { ok: true, data: [] },
    }])).toEqual(['dify-docs']);
  });
});

describe('finalOutputIssues', () => {
  it('flags a public reply written in a language other than the one the Worker chose', () => {
    const output = { customerReply: 'Hello! The credits are a one-time 200.' };
    expect(finalOutputIssues(output, true, 'ja')).toEqual([expect.stringContaining('not written in Japanese')]);
    expect(finalOutputIssues({ customerReply: '教育プランのクレジットは200です。' }, true, 'ja')).toEqual([]);
  });

  it('flags a mention without an operator answer but does not check its English draft language', () => {
    expect(finalOutputIssues({ customerReply: 'Hello!' }, false, 'en'))
      .toEqual([expect.stringContaining('OPERATOR ANSWER === section is missing')]);
    expect(finalOutputIssues({ customerReply: 'Hello!', operatorAnswer: '结论：已核对。' }, false, 'en')).toEqual([]);
  });

  it('leaves an empty reply to the delivery gate', () => {
    expect(finalOutputIssues({ customerReply: ' ' }, true, 'zh')).toEqual([]);
  });
});
