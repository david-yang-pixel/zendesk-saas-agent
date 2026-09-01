import { describe, expect, it } from 'vitest'
import { createAdminApiClient, type AdminApiTransport } from './client'

function transportFor(payloads: Record<string, unknown>): AdminApiTransport & { calls: Array<{ path: string; init?: RequestInit }> } {
  const calls: Array<{ path: string; init?: RequestInit }> = []
  return {
    calls,
    async request<T>(path: string, init?: RequestInit): Promise<T> {
      calls.push({ path, init })
      return payloads[path] as T
    },
  }
}

describe('Admin API client', () => {
  it('maps versioned config and never requires a credential value', async () => {
    const transport = transportFor({
      '/api/config': {
        revision: 'r1',
        created_at: '2026-09-01T00:00:00Z',
        actor: 'bootstrap',
        config: {
          desired_state: 'running',
          root_model: { provider: 'openai', model: 'gpt-root', credential_ref: 'secret/root' },
          subagent_slots: [{ slot_id: 'runtime', enabled: true, provider: 'openai', model: 'gpt-child', credential_ref: 'secret/child', max_parallel: 1 }],
          rules: { saas_tag: 'saas', saas_assignee: 'saas-support', statuses: ['new', 'open'], human_aliases: ['human'], reply_enabled: false, default_locale: 'en', footers: {} },
          enabled_skills: ['core'],
          enabled_tools: ['zendesk'],
          context_window_tokens: 128000,
          soft_compaction_percent: 60,
          hard_checkpoint_percent: 80,
          max_tool_rounds: 24,
        },
      },
    })

    const snapshot = await createAdminApiClient(transport).getConfig()

    expect(snapshot.config.rootModel.credentialRef).toBe('secret/root')
    expect(snapshot.config.subagentSlots[0]?.credentialRef).toBe('secret/child')
    expect(snapshot.config.rules.saasTag).toBe('saas')
    expect(transport.calls).toEqual([{ path: '/api/config', init: undefined }])
  })

  it('sends only a reference and the optimistic revision when saving config', async () => {
    const transport = transportFor({
      '/api/config': {
        revision: 'r2',
        created_at: '2026-09-01T00:00:00Z',
        actor: 'operator',
        config: {
          desired_state: 'paused',
          root_model: { provider: 'openai', model: 'gpt-root', credential_ref: 'secret/root' },
          subagent_slots: [],
          rules: { saas_tag: 'saas', saas_assignee: 'saas-support', statuses: ['new', 'open'], human_aliases: ['human'], reply_enabled: false, default_locale: 'en', footers: {} },
          enabled_skills: [],
          enabled_tools: [],
          context_window_tokens: 128000,
          soft_compaction_percent: 60,
          hard_checkpoint_percent: 80,
          max_tool_rounds: 24,
        },
      },
    })
    const config = {
      desiredState: 'paused' as const,
      rootModel: { provider: 'openai', model: 'gpt-root', credentialRef: 'secret/root' },
      subagentSlots: [],
      rules: { saasTag: 'saas', assignee: 'saas-support', statuses: ['new', 'open'], humanAliases: ['human'], replyEnabled: false, footerLocale: 'en', footers: {} },
      enabledSkills: [],
      enabledTools: [],
      contextWindowTokens: 128000,
      softCompactionPercent: 60,
      hardCheckpointPercent: 80,
      maxToolRounds: 24,
    }

    await createAdminApiClient(transport).saveConfig({ expectedRevision: 'r1', config })

    expect(transport.calls[0]?.path).toBe('/api/config')
    expect(transport.calls[0]?.init?.method).toBe('PUT')
    expect(JSON.parse(String(transport.calls[0]?.init?.body))).toEqual({
      expected_revision: 'r1',
      config: expect.objectContaining({
        root_model: expect.objectContaining({ credential_ref: 'secret/root' }),
      }),
    })
  })

  it('preserves unknown persisted run activity as null instead of inventing zeros', async () => {
    const transport = transportFor({
      '/api/runs': {
        total: 1,
        items: [{
          ticket_id: 3726,
          subject: 'Ticket #3726',
          status: 'completed',
          status_label: '已完成',
          started_at: '2026-09-01T00:00:00Z',
          duration: '1m 0s',
          steps: null,
          session_id: 'session-3726',
          question: null,
          todo_completed: null,
          todo_total: null,
          tool_calls: null,
          subagents: null,
          evidence: null,
          checkpoint: null,
          sandbox: 'destroyed',
          outcome: 'completed',
        }],
      },
    })

    const result = await createAdminApiClient(transport).getRuns()

    expect(result.items[0]).toMatchObject({
      question: null,
      todoCompleted: null,
      todoTotal: null,
      toolCalls: null,
      subagents: null,
      evidence: null,
      checkpoint: null,
      steps: null,
    })
  })
})
