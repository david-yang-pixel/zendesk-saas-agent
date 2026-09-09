import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'
import App from './App.vue'
import { createAppRouter } from './router'

async function mountConsole(path = '/') {
  const router = createAppRouter(createMemoryHistory())
  router.push(path)
  await router.isReady()
  const wrapper = mount(App, { props: { skipAuthentication: true }, global: { plugins: [router] } })
  return { wrapper, router }
}

describe('Casework 管理后台壳', () => {
  beforeAll(() => {
    window.scrollTo = () => undefined
  })

  it('展示导航菜单', async () => {
    const { wrapper } = await mountConsole()

    expect(wrapper.get('nav[aria-label="主导航"]')).toBeTruthy()
    expect(wrapper.get('[data-nav="runs"]').text()).toContain('工单日志')
    expect(wrapper.get('[data-nav="model"]').text()).toContain('模型配置')
    expect(wrapper.get('[data-nav="rules"]').text()).toContain('规则与安全')
    expect(wrapper.get('[data-nav="tools"]').text()).toContain('工具中心')
  })

  it('默认进入工单日志页面', async () => {
    const { wrapper, router } = await mountConsole()

    expect(router.currentRoute.value.path).toBe('/runs')
    expect(wrapper.text()).toContain('工单日志')
    expect(wrapper.text()).toContain('工单推演流水')
  })

  it('点击导航后更新 URL 与当前页面', async () => {
    const { wrapper, router } = await mountConsole()

    await wrapper.get('[data-nav="model"]').trigger('click')
    await flushPromises()
    await nextTick()

    expect(router.currentRoute.value.path).toBe('/model')
    expect(wrapper.text()).toContain('模型配置与实时测速中心')
  })

  it.each([
    ['/rules', '准入规则与人工保护', 'Webhook 标签'],
    ['/runs', '工单推演流水', '选择左侧工单以回放推演'],
    ['/tools', '全能力 MCP 工具总线', 'MCP Mesh'],
    ['/model', '模型配置与实时测速中心', '模型网关参数'],
  ])('路由 %s 渲染对应页面内容', async (path, title, marker) => {
    const { wrapper } = await mountConsole(path)
    await flushPromises()

    expect(wrapper.text()).toContain(title)
    expect(wrapper.text()).toContain(marker)
  })

  it('未知路径回退到工单日志', async () => {
    const { wrapper, router } = await mountConsole('/not-a-page')

    expect(router.currentRoute.value.path).toBe('/runs')
    expect(wrapper.text()).toContain('工单日志')
  })
})
