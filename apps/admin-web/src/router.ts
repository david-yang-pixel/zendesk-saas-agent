import { createRouter, createWebHistory, type RouterHistory } from 'vue-router'
import RunsPage from './pages/RunsPage.vue'
import HarnessPage from './pages/HarnessPage.vue'
import RulesPage from './pages/RulesPage.vue'
import McpMeshPage from './pages/McpMeshPage.vue'
import { adminBasePath } from './base-path'

export function createAppRouter(history: RouterHistory = createWebHistory(adminBasePath())) {
  return createRouter({
    history,
    routes: [
      { path: '/', redirect: '/runs' },
      { path: '/runs', name: 'runs', component: RunsPage, meta: { label: '工单日志' } },
      { path: '/logs', redirect: '/runs' },
      { path: '/traces', redirect: '/runs' },
      { path: '/model', name: 'model', component: HarnessPage, meta: { label: '模型配置' } },
      { path: '/harness', redirect: '/model' },
      { path: '/rules', name: 'rules', component: RulesPage, meta: { label: '规则与安全' } },
      { path: '/safety', redirect: '/rules' },
      { path: '/tools', name: 'tools', component: McpMeshPage, meta: { label: '工具中心' } },
      { path: '/mcp', redirect: '/tools' },
      { path: '/skills', redirect: '/model' },
      { path: '/:pathMatch(.*)*', redirect: '/runs' },
    ],
    scrollBehavior: () => ({ top: 0 }),
  })
}
