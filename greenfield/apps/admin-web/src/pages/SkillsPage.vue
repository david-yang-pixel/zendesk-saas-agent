<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import type { RegistryData, StatusTone } from '../types'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi
const activeTab = ref<'skills' | 'tools'>('skills')
const testedTool = ref<string | null>(null)
const registry = ref<RegistryData>({ skills: [], tools: [] })
const loading = ref(true)
const errorMessage = ref('')

function connectionTone(status: 'connected' | 'degraded' | 'untested'): StatusTone {
  if (status === 'connected') return 'success'
  if (status === 'degraded') return 'warning'
  return 'neutral'
}

async function loadRegistry() {
  loading.value = true
  errorMessage.value = ''
  try {
    registry.value = await api.getRegistry()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取能力注册表失败'
  } finally {
    loading.value = false
  }
}

async function testConnection(toolId: string) {
  testedTool.value = toolId
  errorMessage.value = ''
  try {
    const result = await api.testTool(toolId)
    registry.value = {
      ...registry.value,
      tools: registry.value.tools.map((tool) => tool.id === toolId ? {
        ...tool,
        connection: result.status,
        latency: result.latencyMs === null ? '—' : `${result.latencyMs} ms`,
      } : tool),
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '连接测试失败'
  } finally {
    if (testedTool.value === toolId) testedTool.value = null
  }
}

onMounted(() => { void loadRegistry() })
</script>

<template>
  <div class="page-view">
    <div class="page-heading">
      <div>
        <p class="eyebrow">CAPABILITY REGISTRY / VERSIONED</p>
        <h1>Skill 与工具</h1>
        <p class="page-description">查看可渐进加载的调查 Skill、Tool contract 和连接状态。</p>
      </div>
      <div class="heading-actions"><button type="button" class="button button--secondary" :disabled="loading" @click="loadRegistry"><AppIcon name="refresh" :size="15" />{{ loading ? '刷新中…' : '刷新 registry' }}</button><button type="button" class="button button--primary" disabled><AppIcon name="spark" :size="15" />注册能力</button></div>
    </div>

    <div v-if="errorMessage" class="notice-banner notice-banner--danger" role="alert"><AppIcon name="alert" :size="18" /><div><strong>能力注册表不可用</strong><span>{{ errorMessage }}</span></div></div>

    <div class="registry-summary"><div class="registry-summary__item"><span class="registry-summary__icon registry-summary__icon--violet"><AppIcon name="spark" :size="18" /></span><div><strong>{{ registry.skills.length }} 个 Skill</strong><span>{{ registry.skills.filter((skill) => skill.status === 'core').length }} core · {{ registry.skills.filter((skill) => skill.status === 'enabled').length }} enabled · {{ registry.skills.filter((skill) => skill.status === 'disabled').length }} disabled</span></div></div><div class="registry-summary__divider" /><div class="registry-summary__item"><span class="registry-summary__icon registry-summary__icon--blue"><AppIcon name="terminal" :size="18" /></span><div><strong>{{ registry.tools.length }} 个 Tool</strong><span>{{ registry.tools.filter((tool) => tool.scope === 'root + subagents').length }} 个可供 Root + Subagents 使用</span></div></div><div class="registry-summary__divider" /><div class="registry-summary__item"><span class="registry-summary__icon registry-summary__icon--green"><AppIcon name="check" :size="18" /></span><div><strong>连接健康</strong><span>{{ registry.tools.filter((tool) => tool.connection === 'connected').length }} connected · {{ registry.tools.filter((tool) => tool.connection === 'degraded').length }} degraded</span></div></div></div>

    <section class="panel registry-panel">
      <div class="registry-tabs" role="tablist" aria-label="能力类型"><button type="button" role="tab" :aria-selected="activeTab === 'skills'" class="registry-tab" :class="{ 'registry-tab--active': activeTab === 'skills' }" @click="activeTab = 'skills'"><AppIcon name="spark" :size="16" />技能注册表 <span>{{ registry.skills.length }}</span></button><button type="button" role="tab" :aria-selected="activeTab === 'tools'" class="registry-tab" :class="{ 'registry-tab--active': activeTab === 'tools' }" @click="activeTab = 'tools'"><AppIcon name="terminal" :size="16" />工具 contracts <span>{{ registry.tools.length }}</span></button></div>
      <div v-if="activeTab === 'skills'" class="table-wrap">
        <table class="data-table registry-table"><thead><tr><th>Skill</th><th>描述</th><th>版本</th><th>分配范围</th><th>状态</th><th>最近更新</th><th /></tr></thead><tbody><tr v-for="skill in registry.skills" :key="skill.id"><td><div class="resource-name"><span class="resource-icon resource-icon--skill"><AppIcon name="spark" :size="14" /></span><div><strong>{{ skill.name }}</strong><small>{{ skill.id }}</small></div></div></td><td class="description-cell">{{ skill.description }}</td><td><span class="version-pill">{{ skill.version }}</span></td><td class="muted-cell">{{ skill.assignedTodos }}</td><td><StatusBadge :label="skill.status === 'core' ? 'Core' : skill.status === 'enabled' ? 'Enabled' : 'Disabled'" :tone="skill.status === 'disabled' ? 'neutral' : skill.status === 'core' ? 'info' : 'success'" /></td><td class="muted-cell">{{ skill.updatedAt }}</td><td><button type="button" class="icon-button icon-button--small" :aria-label="`打开 ${skill.name}`"><AppIcon name="chevron" :size="15" /></button></td></tr></tbody></table>
      </div>
      <div v-else class="table-wrap">
        <table class="data-table registry-table"><thead><tr><th>Tool</th><th>描述</th><th>可用范围</th><th>Contract</th><th>连接</th><th>延迟</th><th /></tr></thead><tbody><tr v-for="tool in registry.tools" :key="tool.id"><td><div class="resource-name"><span class="resource-icon resource-icon--tool"><AppIcon name="terminal" :size="14" /></span><div><strong>{{ tool.name }}</strong><small>{{ tool.id }}</small></div></div></td><td class="description-cell">{{ tool.description }}</td><td class="muted-cell">{{ tool.scope }}</td><td><span class="version-pill">{{ tool.contract }}</span></td><td><StatusBadge :label="testedTool === tool.id ? '测试中…' : tool.connection === 'connected' ? 'Connected' : tool.connection === 'degraded' ? 'Degraded' : 'Untested'" :tone="testedTool === tool.id ? 'info' : connectionTone(tool.connection)" /></td><td class="mono-cell">{{ tool.latency }}</td><td><button type="button" class="text-button text-button--compact" @click="testConnection(tool.id)">{{ testedTool === tool.id ? '运行中' : '测试连接' }}</button></td></tr></tbody></table>
      </div>
    </section>

    <div class="registry-footnote"><AppIcon name="shield" :size="15" /><span>所有 Tool result 都必须通过 typed contract 校验并绑定 evidence_id，原始大输出存放在 sandbox artifact。</span><a href="#" @click.prevent>查看 contract 规范 <AppIcon name="external" :size="12" /></a></div>
  </div>
</template>
