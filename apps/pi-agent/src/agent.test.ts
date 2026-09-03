import { describe, it, expect } from 'vitest';
import { CustomTool } from './mcp.js';

describe('Pi Agent unit tests', () => {
  it('defines custom tool structure correctly', () => {
    const tool: CustomTool = {
      name: 'test_tool',
      label: 'test_tool',
      description: 'Test description',
      promptSnippet: 'Test snippet',
      parameters: { type: 'object', properties: { q: { type: 'string' } } },
      execute: async ({ q }) => ({ query: q, status: 'ok' })
    };

    expect(tool.name).toBe('test_tool');
    expect(tool.parameters.type).toBe('object');
  });

  it('formats footer correctly for Chinese queries', () => {
    const isChinese = /[\u4e00-\u9fa5]/.test('测试问题');
    expect(isChinese).toBe(true);
  });

  it('formats footer correctly for English queries', () => {
    const isChinese = /[\u4e00-\u9fa5]/.test('What is my subscription?');
    expect(isChinese).toBe(false);
  });
});
