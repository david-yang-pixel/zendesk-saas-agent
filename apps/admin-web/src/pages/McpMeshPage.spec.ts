import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import McpMeshPage from './McpMeshPage.vue'
import { ADMIN_API_KEY, type AdminApiClient } from '../api/client'

const onlineService = {
  id: 'openconnector',
  name: 'openconnector',
  label: 'OpenConnector Read-only Investigation Gateway',
  service_type: 'query',
  endpoint: 'https://openconnector.example/mcp',
  online: true,
  latency_ms: 14,
  tool_count: 1,
  tools: [{ name: 'list_connections', description: 'Lists connections', inputSchema: { type: 'object' } }],
}

function mountPage(getMcpMesh: AdminApiClient['getMcpMesh'], testTool = vi.fn()) {
  return mount(McpMeshPage, {
    global: {
      provide: {
        [ADMIN_API_KEY as symbol]: { getMcpMesh, testTool } as unknown as AdminApiClient,
      },
    },
  })
}

describe('McpMeshPage live state', () => {
  it('does not show fabricated online tools before the probe completes', async () => {
    let resolveLoad!: (value: { services: any[]; total_tools: number }) => void
    const getMcpMesh = vi.fn(() => new Promise<{ services: any[]; total_tools: number }>(resolve => {
      resolveLoad = resolve
    }))
    const wrapper = mountPage(getMcpMesh)
    await nextTick()

    expect(wrapper.text()).toContain('正在读取实时工具状态')
    expect(wrapper.text()).not.toContain('OpenConnector Read-only Investigation Gateway')

    resolveLoad({ services: [onlineService], total_tools: 1 })
    await flushPromises()

    expect(wrapper.text()).toContain('OpenConnector Read-only Investigation Gateway')
    expect(wrapper.text()).toContain('1 个可用工具')
    expect(wrapper.text()).not.toContain('open-connector.yyg066340.workers.dev')
  })

  it('shows a failed live probe instead of claiming tool execution succeeded', async () => {
    const getMcpMesh = vi.fn()
      .mockResolvedValueOnce({ services: [onlineService], total_tools: 1 })
      .mockResolvedValueOnce({
        services: [{ ...onlineService, online: false, tool_count: 0, tools: [], error: 'HTTP 503' }],
        total_tools: 0,
      })
    const testTool = vi.fn()
    const wrapper = mountPage(getMcpMesh, testTool)
    await flushPromises()

    await wrapper.find('.btn-run-probe').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('连接不可用')
    expect(wrapper.text()).toContain('HTTP 503')
    expect(wrapper.text()).toContain('0 个可用工具')
    expect(testTool).not.toHaveBeenCalled()
  })

  it('shows the reason for an initially offline service', async () => {
    const wrapper = mountPage(vi.fn().mockResolvedValue({
      services: [{ ...onlineService, online: false, tool_count: 0, tools: [], error: 'HTTP 401' }],
      total_tools: 0,
    }))
    await flushPromises()

    expect(wrapper.text()).toContain('HTTP 401')
    expect(wrapper.text()).toContain('0 个可用工具')
  })

  it('does not retain fabricated service status when the API fails', async () => {
    const wrapper = mountPage(vi.fn().mockRejectedValue(new Error('API unavailable')))
    await flushPromises()

    expect(wrapper.text()).toContain('API unavailable')
    expect(wrapper.text()).toContain('暂无实时工具状态')
    expect(wrapper.text()).not.toContain('OpenConnector Read-only Investigation Gateway')
  })
})
