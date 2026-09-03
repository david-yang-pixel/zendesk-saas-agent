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
  }
}

describe('RunsPage Trace Player', () => {
  it('renders ticket list and selects trace details', async () => {
    const wrapper = mount(RunsPage, { global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture() } } })
    await flushPromises()

    expect(wrapper.text()).toContain('工单推演流水')
    expect(wrapper.text()).toContain('#3726')
    expect(wrapper.text()).toContain('customer@acme.com')
    expect(wrapper.text()).toContain('深度思考链')
    expect(wrapper.text()).toContain('signoz_search_logs')
    expect(wrapper.text()).toContain('Zendesk Browser Relay 前台投递回执')
  })
})
