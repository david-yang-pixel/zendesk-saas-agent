import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'
import App from './App.vue'
import { createAppRouter } from './router'

async function mountConsole(path = '/') {
  const router = createAppRouter(createMemoryHistory())
  router.push(path)
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } })
  return { wrapper, router }
}

describe('Casework 管理后台壳', () => {
  beforeAll(() => {
    window.scrollTo = () => undefined
  })

  it('展示五个一级页面的可访问导航', async () => {
    const { wrapper } = await mountConsole()

    expect(wrapper.get('nav[aria-label="主导航"]'))
      .toBeTruthy()
    expect(wrapper.get('[data-nav="overview"]').text()).toContain('运行总览')
    expect(wrapper.get('[data-nav="harness"]').text()).toContain('Harness 配置')
    expect(wrapper.get('[data-nav="rules"]').text()).toContain('处理规则')
    expect(wrapper.get('[data-nav="skills"]').text()).toContain('Skill 与工具')
    expect(wrapper.get('[data-nav="runs"]').text()).toContain('工单运行')
  })

  it('默认进入运行总览并展示运行指标', async () => {
    const { wrapper } = await mountConsole()

    expect(wrapper.get('h1').text()).toBe('运行总览')
    expect(wrapper.findAll('[data-testid="metric-card"]')).toHaveLength(4)
    expect(wrapper.get('[data-testid="agent-status"]').text()).toContain('运行中')
  })

  it('点击导航后更新 URL 与当前页面', async () => {
    const { wrapper, router } = await mountConsole()

    await wrapper.get('[data-nav="harness"]').trigger('click')
    await flushPromises()
    await nextTick()

    expect(router.currentRoute.value.path).toBe('/harness')
    expect(wrapper.get('h1').text()).toBe('Harness 配置')
    expect(wrapper.get('[data-testid="root-model"]').text()).toContain('Root Agent')
  })

  it.each([
    ['/rules', '处理规则', 'Webhook 标签'],
    ['/skills', 'Skill 与工具', '技能注册表'],
    ['/runs', '工单运行', '运行记录'],
  ])('路由 %s 渲染对应页面内容', async (path, title, marker) => {
    const { wrapper } = await mountConsole(path)

    expect(wrapper.get('h1').text()).toBe(title)
    expect(wrapper.text()).toContain(marker)
  })

  it('总览页可切换 Agent desired state', async () => {
    const { wrapper } = await mountConsole()
    const toggle = wrapper.get('[data-testid="agent-toggle"]')

    await toggle.trigger('click')

    expect(wrapper.get('[data-testid="agent-status"]').text()).toContain('已暂停')
    expect(toggle.attributes('aria-pressed')).toBe('true')
  })

  it('未知路径回退到运行总览', async () => {
    const { wrapper, router } = await mountConsole('/not-a-page')

    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.get('h1').text()).toBe('运行总览')
  })
})
