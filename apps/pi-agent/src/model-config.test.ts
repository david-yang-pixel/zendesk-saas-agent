import { describe, expect, it } from 'vitest';
import { MODEL_NOT_CONFIGURED, parseModelConfig } from './model-config.js';

describe('parseModelConfig', () => {
  it('reads the model saved on the Admin page', () => {
    expect(parseModelConfig({
      provider: 'openai', base_url: ' https://api.example.test/v1 ', model: 'gpt-6-sol', api_key: 'secret',
    })).toEqual({ baseUrl: 'https://api.example.test/v1', model: 'gpt-6-sol', apiKey: 'secret' });
  });

  it('reads the reasoning level detected and chosen on the Admin page', () => {
    const base = { base_url: 'https://api.example.test/v1', model: 'gpt-6-sol', api_key: 'secret' };
    expect(parseModelConfig({ ...base, reasoning_levels: ['low', 'high', 'turbo'], reasoning_effort: 'high' }).reasoning)
      .toEqual({ levels: ['low', 'high'], effort: 'high' });
    expect(parseModelConfig({ ...base, reasoning_levels: ['low'], reasoning_effort: 'high' }).reasoning)
      .toEqual({ levels: ['low'], effort: undefined });
    expect(parseModelConfig({ ...base, reasoning_levels: [], reasoning_effort: null }).reasoning)
      .toEqual({ levels: [], effort: undefined });
    // Saved before levels were detected: the agent keeps its endpoint default.
    expect(parseModelConfig(base).reasoning).toBeUndefined();
  });

  it.each([
    undefined,
    {},
    { base_url: 'https://api.example.test/v1', model: 'gpt-6-sol' },
    { base_url: 'https://api.example.test/v1', api_key: 'secret' },
    { model: 'gpt-6-sol', api_key: 'secret' },
    { base_url: ' ', model: 'gpt-6-sol', api_key: 'secret' },
  ])('refuses an incomplete configuration instead of using a default model: %j', (value) => {
    expect(() => parseModelConfig(value)).toThrow(MODEL_NOT_CONFIGURED);
  });
});
