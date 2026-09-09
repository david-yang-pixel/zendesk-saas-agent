import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import HarnessPage from './HarnessPage.vue'
import { ADMIN_API_KEY, type AdminApiClient } from '../api/client'

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
    getModelSettings: async () => ({
      provider: 'openai',
      base_url: 'https://api.tokener.dev/v1',
      model: 'gpt-5.6-terra',
      api_key_masked: 'sk-hIey...GlqfZ',
      has_key: true,
      reasoning: true,
      timeout_seconds: 120,
    }),
    saveModelSettings: async (payload) => {
      saveCalls.push(payload)
      return { ok: true, message: 'saved' }
    },
    testModelConnection: async () => ({
      ok: true,
      status_code: 200,
      latency_ms: 125,
      message: 'Connection successful',
      sample_text: 'pong',
    }),
    getMcpMesh: async () => ({ services: [], total_tools: 0 }),
    getSkills: async () => [],
    saveSkill: async () => ({ ok: true, message: 'saved' }),
    skipJob: async () => ({ ok: true, job_id: 'job-1', message: 'skipped' }),
    escalateJob: async () => ({ ok: true, ticket_id: 1, message: 'escalated' }),
    getControlMode: async () => ({ mode: 'live' as const }),
    setControlMode: async () => ({ ok: true, mode: 'live' as const }),
    getSafetySettings: async () => ({
      escalation_keywords: [],
      auto_escalate_on_fail: true,
      max_retries: 2,
      ticket_blacklist: [],
      requester_blacklist: [],
      feishu_webhook_url: '',
    }),
    saveSafetySettings: async () => ({ ok: true, message: 'saved' }),
    testFeishuWebhook: async () => ({ ok: true, status_code: 200, latency_ms: 15, message: 'ok' }),
    getMeshHealth: async () => ({ items: [], overall_status: 'healthy' as const }),
    getLiveLogs: async () => ({ lines: ['[Worker] Mock log line'], total: 1 }),
  }
}

describe('HarnessPage Model Studio', () => {
  it('renders model studio settings and allows ping testing', async () => {
    const wrapper = mount(HarnessPage, { global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture() } } })
    await flushPromises()

    expect(wrapper.text()).toContain('模型配置与实时测速中心')
    expect(wrapper.text()).toContain('https://api.tokener.dev/v1')
    expect(wrapper.text()).toContain('gpt-5.6-terra')

    await wrapper.find('.btn-test').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('模型连通测试通过')
    expect(wrapper.text()).toContain('125 ms')
  })
})
