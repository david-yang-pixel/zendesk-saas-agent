import { createRouter, createWebHistory, type RouterHistory } from 'vue-router'
import OverviewPage from './pages/OverviewPage.vue'
import HarnessPage from './pages/HarnessPage.vue'
import RulesPage from './pages/RulesPage.vue'
import SkillsPage from './pages/SkillsPage.vue'
import RunsPage from './pages/RunsPage.vue'
import { adminBasePath } from './base-path'

export function createAppRouter(history: RouterHistory = createWebHistory(adminBasePath())) {
  return createRouter({
    history,
    routes: [
      { path: '/', name: 'overview', component: OverviewPage, meta: { label: '运行总览' } },
      { path: '/harness', name: 'harness', component: HarnessPage, meta: { label: 'Harness 配置' } },
      { path: '/rules', name: 'rules', component: RulesPage, meta: { label: '处理规则' } },
      { path: '/skills', name: 'skills', component: SkillsPage, meta: { label: 'Skill 与工具' } },
      { path: '/runs', name: 'runs', component: RunsPage, meta: { label: '工单运行' } },
      { path: '/:pathMatch(.*)*', redirect: '/' },
    ],
    scrollBehavior: () => ({ top: 0 }),
  })
}
