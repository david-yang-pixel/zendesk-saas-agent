// The Admin 模型配置 page (system_settings.model_config) is the only source of
// model settings. There is no built-in default: a missing setting fails the
// job instead of silently running a different model.

/** Thinking levels the Admin page can detect and save for a model. */
export const REASONING_LEVELS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh'] as const;
export type ReasoningLevel = typeof REASONING_LEVELS[number];

export interface ModelReasoning {
  /** Levels the model accepted when probed; empty when it accepts no explicit effort. */
  levels: ReasoningLevel[];
  /** The level the operator chose; always one of `levels`. */
  effort?: ReasoningLevel;
}

export interface ModelConfig {
  baseUrl: string;
  model: string;
  apiKey: string;
  /** Absent for configurations saved before reasoning levels were detected. */
  reasoning?: ModelReasoning;
}

export const MODEL_NOT_CONFIGURED =
  'Model is not configured. Set the model, base URL and API key on the Admin 模型配置 page.';

function isReasoningLevel(value: unknown): value is ReasoningLevel {
  return (REASONING_LEVELS as readonly unknown[]).includes(value);
}

export function parseModelConfig(value: unknown): ModelConfig {
  const settings = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
  const text = (key: string) => (typeof settings[key] === 'string' ? (settings[key] as string).trim() : '');
  const config: ModelConfig = { baseUrl: text('base_url'), model: text('model'), apiKey: text('api_key') };
  if (!config.baseUrl || !config.model || !config.apiKey) {
    throw new Error(MODEL_NOT_CONFIGURED);
  }
  if (Array.isArray(settings.reasoning_levels)) {
    const levels = settings.reasoning_levels.filter(isReasoningLevel);
    const effort = settings.reasoning_effort;
    config.reasoning = { levels, effort: isReasoningLevel(effort) && levels.includes(effort) ? effort : undefined };
  }
  return config;
}
