import { createRouter, createWebHistory, type RouterHistory } from 'vue-router'
import OverviewPage from './pages/OverviewPage.vue'
import HarnessPage from './pages/HarnessPage.vue'
import RulesPage from './pages/RulesPage.vue'
import SkillsPage from './pages/SkillsPage.vue'
import RunsPage from './pages/RunsPage.vue'
import McpMeshPage from './pages/McpMeshPage.vue'
import { adminBasePath } from './base-path'

export function createAppRouter(history: RouterHistory = createWebHistory(adminBasePath())) {
  return createRouter({
    history,
    routes: [
      { path: '/', name: 'overview', component: OverviewPage, meta: { label: '运行总览' } },
      { path: '/runs', name: 'runs', component: RunsPage, meta: { label: '工单推演' } },
      { path: '/traces', redirect: '/runs' },
      { path: '/harness', name: 'harness', component: HarnessPage, meta: { label: '模型配置' } },
      { path: '/model', redirect: '/harness' },
      { path: '/mcp', name: 'mcp', component: McpMeshPage, meta: { label: 'MCP 总线' } },
      { path: '/skills', name: 'skills', component: SkillsPage, meta: { label: '技能中心' } },
      { path: '/rules', name: 'rules', component: RulesPage, meta: { label: '准入规则' } },
      { path: '/:pathMatch(.*)*', redirect: '/' },
    ],
    scrollBehavior: () => ({ top: 0 }),
  })
}
