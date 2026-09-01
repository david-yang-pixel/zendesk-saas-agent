import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import RunsPage from './RunsPage.vue'
import { ADMIN_API_KEY, type AdminApiClient } from '../api/client'

const unknownRun = {
  ticketId: 3726,
  subject: 'Ticket #3726',
  status: 'completed' as const,
  statusLabel: '已完成',
  startedAt: '2026-09-01T00:00:00Z',
  duration: '1m 0s',
  steps: null,
  sessionId: 'session-3726',
  configRevision: null,
  question: null,
  todoCompleted: null,
  todoTotal: null,
  toolCalls: null,
  subagents: null,
  evidence: null,
  checkpoint: null,
  sandbox: 'destroyed' as const,
  outcome: 'completed',
}

function apiFixture(): AdminApiClient {
  return {
    getConfig: async () => { throw new Error('not used') },
    saveConfig: async () => { throw new Error('not used') },
    getSandboxPolicy: async () => { throw new Error('not used') },
    getOverview: async () => { throw new Error('not used') },
    getRuns: async () => ({ items: [unknownRun], total: 1 }),
    getRun: async () => unknownRun,
    getRegistry: async () => ({ skills: [], tools: [] }),
    testTool: async () => ({ toolId: 'unknown', status: 'untested', latencyMs: null, errorCode: 'unknown_tool' as const, detail: 'Tool is not registered' }),
    getModelCredentials: async () => [],
    replaceModelCredential: async () => { throw new Error('not used') },
  }
}

describe('RunsPage evidence honesty', () => {
  it('renders unknown activity explicitly when Postgres has no runtime projection', async () => {
    const wrapper = mount(RunsPage, { global: { provide: { [ADMIN_API_KEY as symbol]: apiFixture() } } })
    await flushPromises()

    expect(wrapper.text()).toContain('未知（尚未持久化）')
    expect(wrapper.text()).not.toContain('0 / 0')

    await wrapper.get('.run-table-row').trigger('click')
    expect(wrapper.get('[aria-label="运行详情"]').text()).toContain('Evidence 未持久化')
    expect(wrapper.get('[aria-label="运行详情"]').text()).toContain('Subagents')
    expect(wrapper.get('[aria-label="运行详情"]').text()).toContain('Checkpoint')
    expect(wrapper.get('[aria-label="运行详情"]').text()).toContain('未知（尚未持久化）')
    expect(wrapper.get('[aria-label="运行详情"]').text()).toContain('Config revision')
  })
})
