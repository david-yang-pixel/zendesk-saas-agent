import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SkillsPage from './SkillsPage.vue'
import { ADMIN_API_KEY, type AdminApiClient } from '../api/client'

const mockSkills = [
  {
    name: 'core-casework',
    description: 'Casework core investigation standards',
    path: '/skills/core-casework/SKILL.md',
    content: '# Core Casework\n\nPreserve evidence...',
  },
]

function apiFixture(saveCalls: any[] = []): AdminApiClient {
  return {
    getSession: async () => ({ username: 'admin', csrfToken: 'csrf', forcePasswordChange: false }),
    login: async () => ({ username: 'admin', csrfToken: 'csrf', forcePasswordChange: false }),
    logout: async () => undefined,
    changePassword: async () => undefined,
    getConfig: async () => { throw new Error('not used') },
    saveConfig: async () => { throw new Error('not used') },
    getSandboxPolicy: async () => { throw new Error('not used') },
    getOverview: async () => { throw new Error('not used') },
    getRuns: async () => ({ items: [], total: 0 }),
    getRun: async () => { throw new Error('not used') },
    getRegistry: async () => ({ skills: [], tools: [] }),
    testTool: async () => ({ toolId: 'unknown', status: 'untested', latencyMs: null, errorCode: 'unknown_tool' as const, detail: 'Tool is not registered' }),
    getModelCredentials: async () => [],
    replaceModelCredential: async () => { throw new Error('not used') },
    discoverModels: async () => { throw new Error('not used') },
    getTraces: async () => ({ items: [], total: 0 }),
    getTrace: async () => { throw new Error('not used') },
    retryJob: async () => ({ ok: true, message: 'requeued' }),
    getModelSettings: async () => { throw new Error('not used') },
    saveModelSettings: async () => { throw new Error('not used') },
    testModelConnection: async () => { throw new Error('not used') },
    getMcpMesh: async () => ({ services: [], total_tools: 0 }),
    getSkills: async () => mockSkills,
    saveSkill: async (name, content) => {
      saveCalls.push({ name, content })
      return { ok: true, message: 'saved' }
    },
  }
}

describe('SkillsPage Skills Hub', () => {
  it('renders skill cards and allows editing markdown content', async () => {
    const saveCalls: any[] = []
    const wrapper = mount(SkillsPage, {
      global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture(saveCalls) } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('技能与 SOP 知识库中心')
    expect(wrapper.text()).toContain('core-casework')
    expect(wrapper.text()).toContain('Casework core investigation standards')
    expect((wrapper.find('textarea.markdown-editor').element as HTMLTextAreaElement).value).toContain('Preserve evidence...')

    await wrapper.find('.btn-save').trigger('click')
    await flushPromises()

    expect(saveCalls.length).toBe(1)
    expect(saveCalls[0].name).toBe('core-casework')
    expect(wrapper.text()).toContain('已保存！Pi Agent 无需重启即可载入新知识。')
  })
})
