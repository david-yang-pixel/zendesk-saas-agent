import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SkillsPage from './SkillsPage.vue'
import { ADMIN_API_KEY, type AdminApiClient } from '../api/client'

function apiFixture(): AdminApiClient {
  return {
    getConfig: async () => { throw new Error('not used') },
    saveConfig: async () => { throw new Error('not used') },
    getSandboxPolicy: async () => { throw new Error('not used') },
    getOverview: async () => { throw new Error('not used') },
    getRuns: async () => ({ items: [], total: 0 }),
    getRun: async () => { throw new Error('not used') },
    getRegistry: async () => ({
      skills: [],
      tools: [{
        id: 'zendesk',
        name: 'Zendesk',
        description: 'read-only',
        scope: 'root only',
        contract: 'typed',
        connection: 'untested',
        latency: '—',
      }],
    }),
    testTool: async () => ({
      toolId: 'zendesk',
      status: 'degraded' as const,
      latencyMs: null,
      errorCode: 'timeout' as const,
      detail: 'Tool connection probe timed out',
    }),
    getModelCredentials: async () => [],
    replaceModelCredential: async () => { throw new Error('not used') },
  }
}

describe('SkillsPage tool probes', () => {
  it('renders safe probe diagnostics after a read-only connection test', async () => {
    const wrapper = mount(SkillsPage, {
      global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture() } },
    })
    await flushPromises()
    await wrapper.get('[role="tab"][aria-selected="false"]').trigger('click')
    await wrapper.get('button.text-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Tool connection probe timed out')
    expect(wrapper.text()).toContain('Degraded')
    expect(wrapper.text()).not.toContain('Bearer')
  })
})
