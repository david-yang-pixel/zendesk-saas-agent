// The Admin 模型配置 page (system_settings.model_config) is the only source of
// model settings. There is no built-in default: a missing setting fails the
// job instead of silently running a different model.

export interface ModelConfig {
  baseUrl: string;
  model: string;
  apiKey: string;
}

export const MODEL_NOT_CONFIGURED =
  'Model is not configured. Set the model, base URL and API key on the Admin 模型配置 page.';

export function parseModelConfig(value: unknown): ModelConfig {
  const settings = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
  const text = (key: string) => (typeof settings[key] === 'string' ? (settings[key] as string).trim() : '');
  const config = { baseUrl: text('base_url'), model: text('model'), apiKey: text('api_key') };
  if (!config.baseUrl || !config.model || !config.apiKey) {
    throw new Error(MODEL_NOT_CONFIGURED);
  }
  return config;
}
