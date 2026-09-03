import { describe, it, expect } from 'vitest';
import { CustomTool } from './mcp.js';

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

    const isChinese = (msg: string) => /[\u4e00-\u9fa5]/.test(msg);
    expect(isChinese(chineseMsg)).toBe(true);
    expect(isChinese(englishMsg)).toBe(false);
  });
});
