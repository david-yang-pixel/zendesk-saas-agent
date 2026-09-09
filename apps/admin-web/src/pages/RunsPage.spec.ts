import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import RunsPage from './RunsPage.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'

const mockTraceItem = {
  ticket_id: 3726,
  job_id: 'job-3726',
  session_id: 'sess-3726',
  status: 'completed',
  created_at: '2026-09-02T10:00:00Z',
  finished_at: '2026-09-02T10:00:25Z',
  duration: '25s',
  customer_message: 'Why is my workflow returning 500 internal server error?',
  requester_email: 'customer@acme.com',
  model: 'gpt-5.6-terra',
  tool_calls_count: 1,
  reply_preview: 'Hello! After investigating...',
  delivery_status: 'sent',
  has_trace: true,
}

const mockTraceDetail = {
  ticket_id: 3726,
  job_id: 'job-3726',
  status: 'completed',
  created_at: '2026-09-02T10:00:00Z',
  finished_at: '2026-09-02T10:00:25Z',
  duration: '25s',
  customer_message: 'Why is my workflow returning 500 internal server error?',
  requester_email: 'customer@acme.com',
  model: 'gpt-5.6-terra',
  steps: [
    {
      type: 'thinking' as const,
      thinking: 'Investigating customer 500 error in SigNoz.',
    },
    {
      type: 'tool_call' as const,
      toolName: 'signoz_search_logs',
      service: 'signoz',
      arguments: { query: 'status >= 500' },
      result: [{ timestamp: '2026-09-02T09:59:58Z', message: 'timeout' }],
    },
  ],
  final_reply: 'Hello!\n\nWe checked your workflow run.\n\n---\nThis reply was sent by Dify AI Agent.',
  delivery: {
    channel: 'browser-relay',
    status: 'sent',
    total_duration_ms: 1250,
  },
}

function apiFixture(): AdminApiClient {
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
    getTraces: async () => ({ items: [mockTraceItem], total: 1 }),
    getTrace: async () => mockTraceDetail,
    retryJob: async () => ({ ok: true, message: 'requeued' }),
    getModelSettings: async () => ({ provider: 'openai', base_url: 'https://api.tokener.dev/v1', model: 'gpt-5.6-terra', api_key_masked: 'sk-***', has_key: true, reasoning: true, timeout_seconds: 120 }),
    saveModelSettings: async () => ({ ok: true, message: 'saved' }),
    testModelConnection: async () => ({ ok: true, status_code: 200, latency_ms: 100, message: 'ok', sample_text: 'pong' }),
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
    getLiveLogs: async () => ({ lines: ['[Worker] Mock system live log 1'], total: 1 }),
  }
}

describe('RunsPage Trace Player', () => {
  it('renders ticket list and selects trace details', async () => {
    const wrapper = mount(RunsPage, { global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture() } } })
    await flushPromises()

    expect(wrapper.text()).toContain('工单推演流水')
    expect(wrapper.text()).toContain('#3726')
    expect(wrapper.text()).toContain('customer@acme.com')
    expect(wrapper.text()).toContain('工单处理详细日志')
    expect(wrapper.text()).toContain('深度思考链')
    expect(wrapper.text()).toContain('signoz_search_logs')
    expect(wrapper.text()).toContain('输入参数 (Arguments)')
    expect(wrapper.text()).toContain('status >= 500')
    expect(wrapper.text()).toContain('执行结果 (Output)')
    expect(wrapper.text()).toContain('timeout')
    expect(wrapper.text()).toContain('Zendesk Browser Relay 前台投递回执')

    // Test collapsible processing log bar (default collapsed)
    const procToggleBtn = wrapper.find('.btn-toggle-processing-log')
    expect(procToggleBtn.exists()).toBe(true)
    expect(procToggleBtn.text()).toBe('点击展开详细处理日志 ▾')
    const procBody = wrapper.find('.processing-log-body')
    expect(procBody.attributes('style')).toContain('display: none')

    // Expand processing log
    await procToggleBtn.trigger('click')
    expect(procToggleBtn.text()).toBe('收起详细处理日志 ▴')
    expect(procBody.attributes('style') || '').not.toContain('display: none')

    // Test toggle tool collapse / expand inside processing log
    const toggleBtn = wrapper.find('.tool-toggle-btn')
    expect(toggleBtn.exists()).toBe(true)
    expect(toggleBtn.text()).toBe('折叠')
    await toggleBtn.trigger('click')
    expect(toggleBtn.text()).toBe('展开')
  })

  it('renders direct reasoning node when a ticket has no tool calls', async () => {
    const fixture = apiFixture()
    fixture.getTrace = async () => ({
      ...mockTraceDetail,
      ticket_id: 4341,
      steps: [
        {
          type: 'thinking' as const,
          thinking: 'Customer asks about upgrade options. Answering directly from knowledge base.',
        },
      ],
      final_reply: 'You can upgrade your plan in settings.',
    })

    const wrapper = mount(RunsPage, { global: { provide: { [ADMIN_API_KEY as symbol]: fixture } } })
    await flushPromises()

    expect(wrapper.text()).toContain('端到端直接推理')
    expect(wrapper.text()).toContain('基于知识库与规则解答')
    expect(wrapper.text()).toContain('未触发外部 MCP 工具')
  })
})
