import { describe, expect, it } from 'vitest';
import { MODEL_NOT_CONFIGURED, parseModelConfig } from './model-config.js';

describe('parseModelConfig', () => {
  it('reads the model saved on the Admin page', () => {
    expect(parseModelConfig({
      provider: 'openai', base_url: ' https://api.example.test/v1 ', model: 'gpt-6-sol', api_key: 'secret',
    })).toEqual({ baseUrl: 'https://api.example.test/v1', model: 'gpt-6-sol', apiKey: 'secret' });
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
