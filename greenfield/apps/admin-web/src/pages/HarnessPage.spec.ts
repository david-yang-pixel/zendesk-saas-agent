import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import HarnessPage from './HarnessPage.vue'
import { ADMIN_API_KEY, type AdminApiClient } from '../api/client'
import type { AdminConfigSnapshot } from '../types'

const snapshot: AdminConfigSnapshot = {
  revision: 'cfg-1',
  createdAt: '2026-09-01T00:00:00Z',
  actor: 'bootstrap',
  config: {
    desiredState: 'running',
    rootModel: { provider: 'openai', model: 'gpt-5.6-sol', credentialRef: 'secret/root' },
    subagentSlots: [{
      slotId: 'investigator',
      enabled: true,
      provider: 'openai',
      model: 'gpt-5.6-luna',
      credentialRef: 'secret/investigator',
      contextWindowTokens: 32_000,
      maxToolRounds: 12,
      maxParallel: 1,
    }],
    rules: { saasTag: 'saas', assignee: 'saas-support', statuses: ['new', 'open'], humanAliases: ['human'], replyEnabled: false, footerLocale: 'en', footers: {} },
    enabledSkills: [],
    enabledTools: [],
    contextWindowTokens: 128_000,
    softCompactionPercent: 60,
    hardCheckpointPercent: 80,
    maxToolRounds: 24,
  },
}

function apiFixture(saveCalls: Array<Parameters<AdminApiClient['saveConfig']>[0]>): AdminApiClient {
  return {
    getConfig: async () => snapshot,
    saveConfig: async (input) => {
      saveCalls.push(input)
      return { ...snapshot, revision: 'cfg-2', config: input.config }
    },
    getSandboxPolicy: async () => ({
      attempt1: { cpu: 0.5, memoryMib: 1024, timeoutSeconds: 1800 },
      attempt2: { cpu: 1, memoryMib: 1024, timeoutSeconds: 1800 },
      retryCondition: 'resource_exhausted_only',
    }),
    getOverview: async () => { throw new Error('not used') },
    getRuns: async () => ({ items: [], total: 0 }),
    getRun: async () => { throw new Error('not used') },
    getRegistry: async () => ({ skills: [], tools: [] }),
    testTool: async () => ({ toolId: 'unknown', status: 'untested', latencyMs: null, errorCode: 'unknown_tool', detail: 'Tool is not registered' }),
  }
}

describe('HarnessPage independent subagent configuration', () => {
  it('loads and saves every subagent field without inheriting root settings', async () => {
    const saveCalls: Array<Parameters<AdminApiClient['saveConfig']>[0]> = []
    const wrapper = mount(HarnessPage, {
      global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture(saveCalls) } },
    })
    await flushPromises()

    expect((wrapper.get('[data-testid="slot-credential-ref-0"]').element as HTMLInputElement).value).toBe('secret/investigator')
    await wrapper.get('[data-testid="slot-id-0"]').setValue('logs')
    await wrapper.get('[data-testid="slot-provider-0"]').setValue('anthropic')
    await wrapper.get('[data-testid="slot-model-0"]').setValue('claude-child')
    await wrapper.get('[data-testid="slot-credential-ref-0"]').setValue('secret/logs')
    await wrapper.get('[data-testid="slot-context-window-0"]').setValue('24000')
    await wrapper.get('[data-testid="slot-max-tool-rounds-0"]').setValue('8')
    await wrapper.get('[data-testid="slot-max-parallel-0"]').setValue('2')
    await wrapper.get('button[data-testid="save-config"]').trigger('click')
    await flushPromises()

    expect(saveCalls[0]?.expectedRevision).toBe('cfg-1')
    expect(saveCalls[0]?.config.rootModel.model).toBe('gpt-5.6-sol')
    expect(saveCalls[0]?.config.subagentSlots).toEqual([expect.objectContaining({
      slotId: 'logs',
      provider: 'anthropic',
      model: 'claude-child',
      credentialRef: 'secret/logs',
      contextWindowTokens: 24_000,
      maxToolRounds: 8,
      maxParallel: 2,
    })])
  })

  it('can add and remove independent subagent slots with safe reference defaults', async () => {
    const saveCalls: Array<Parameters<AdminApiClient['saveConfig']>[0]> = []
    const wrapper = mount(HarnessPage, {
      global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture(saveCalls) } },
    })
    await flushPromises()

    await wrapper.get('[data-testid="add-subagent"]').trigger('click')
    expect(wrapper.findAll('[data-testid^="subagent-card-"]')).toHaveLength(2)
    expect((wrapper.get('[data-testid="slot-credential-ref-1"]').element as HTMLInputElement).value).toBe('secret/subagent-2')
    await wrapper.get('[data-testid="remove-slot-1"]').trigger('click')
    expect(wrapper.findAll('[data-testid^="subagent-card-"]')).toHaveLength(1)
  })
})
