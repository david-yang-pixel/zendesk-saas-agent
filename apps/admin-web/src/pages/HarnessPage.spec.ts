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
    detectReasoningLevels: async () => ({ ok: true, levels: ['low', 'medium', 'high'], message: '检测到 3 档思考程度' }),
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

  it('leaves an unconfigured model empty instead of prefilling a default', async () => {
    const api = {
      ...apiFixture(),
      getModelSettings: async () => ({
        provider: 'openai', base_url: '', model: '', api_key_masked: '', has_key: false,
      }),
    }
    const wrapper = mount(HarnessPage, { global: { provide: { [ADMIN_API_KEY as symbol]: api } } })
    await flushPromises()

    const values = wrapper.findAll('input.studio-input[type="text"]').map(input => (input.element as HTMLInputElement).value)
    expect(values).toEqual(['', ''])
  })

  it('detects the reasoning levels after a ping test and requires choosing one before saving', async () => {
    const saveCalls: any[] = []
    const wrapper = mount(HarnessPage, { global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture(saveCalls) } } })
    await flushPromises()

    // A configuration saved before detection existed cannot be saved as is.
    await wrapper.find('.btn-save').trigger('click')
    await flushPromises()
    expect(saveCalls).toHaveLength(0)
    expect(wrapper.text()).toContain('保存前需检测该模型支持的思考程度')

    await wrapper.find('.btn-test').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.effort-option').map(option => option.find('code').text())).toEqual(['low', 'medium', 'high'])

    await wrapper.find('.btn-save').trigger('click')
    await flushPromises()
    expect(saveCalls).toHaveLength(0)
    expect(wrapper.text()).toContain('请选择一个思考程度后再保存')

    await wrapper.find('input[type="radio"][value="high"]').setValue()
    await wrapper.find('.btn-save').trigger('click')
    await flushPromises()
    expect(saveCalls).toEqual([expect.objectContaining({ reasoning_levels: ['low', 'medium', 'high'], reasoning_effort: 'high' })])
    // The unused reasoning-mode switch and step timeout are no longer sent.
    expect(saveCalls[0]).not.toHaveProperty('reasoning')
    expect(saveCalls[0]).not.toHaveProperty('timeout_seconds')
  })

  it('needs a new detection after the model changes', async () => {
    const saveCalls: any[] = []
    const api = {
      ...apiFixture(saveCalls),
      getModelSettings: async () => ({
        provider: 'openai', base_url: 'https://api.openai.com/v1', model: 'gpt-6-sol', api_key_masked: 'sk-...', has_key: true,
        reasoning_levels: ['low', 'high'], reasoning_effort: 'high',
      }),
    }
    const wrapper = mount(HarnessPage, { global: { provide: { [ADMIN_API_KEY as symbol]: api } } })
    await flushPromises()
    expect((wrapper.find('input[type="radio"][value="high"]').element as HTMLInputElement).checked).toBe(true)

    await wrapper.findAll('input.studio-input[type="text"]')[1].setValue('gpt-6-terra')
    expect(wrapper.find('.effort-option').exists()).toBe(false)
    await wrapper.find('.btn-save').trigger('click')
    await flushPromises()
    expect(saveCalls).toHaveLength(0)
  })

  it('saves without an effort when the model accepts none', async () => {
    const saveCalls: any[] = []
    const api = { ...apiFixture(saveCalls), detectReasoningLevels: async () => ({ ok: true, levels: [], message: 'none' }) }
    const wrapper = mount(HarnessPage, { global: { provide: { [ADMIN_API_KEY as symbol]: api } } })
    await flushPromises()

    await wrapper.find('.btn-detect').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('该模型/网关不接受指定思考程度')
    await wrapper.find('.btn-save').trigger('click')
    await flushPromises()
    expect(saveCalls).toEqual([expect.objectContaining({ reasoning_levels: [], reasoning_effort: null })])
  })
})
