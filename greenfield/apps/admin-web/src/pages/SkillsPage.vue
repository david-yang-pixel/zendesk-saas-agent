<script setup lang="ts">
import { ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { skills, tools } from '../data/mockData'
import type { StatusTone } from '../types'

const activeTab = ref<'skills' | 'tools'>('skills')
const testedTool = ref<string | null>(null)

function connectionTone(status: 'connected' | 'degraded' | 'untested'): StatusTone {
  if (status === 'connected') return 'success'
  if (status === 'degraded') return 'warning'
  return 'neutral'
}

function testConnection(toolId: string) {
  testedTool.value = toolId
  window.setTimeout(() => { if (testedTool.value === toolId) testedTool.value = null }, 1400)
}
</script>

<template>
  <div class="page-view">
    <div class="page-heading">
      <div>
        <p class="eyebrow">CAPABILITY REGISTRY / VERSIONED</p>
        <h1>Skill 与工具</h1>
        <p class="page-description">查看可渐进加载的调查 Skill、Tool contract 和连接状态。</p>
      </div>
      <div class="heading-actions"><button type="button" class="button button--secondary"><AppIcon name="refresh" :size="15" />刷新 registry</button><button type="button" class="button button--primary"><AppIcon name="spark" :size="15" />注册能力</button></div>
    </div>

    <div class="registry-summary"><div class="registry-summary__item"><span class="registry-summary__icon registry-summary__icon--violet"><AppIcon name="spark" :size="18" /></span><div><strong>{{ skills.length }} 个 Skill</strong><span>1 core · {{ skills.filter((skill) => skill.status === 'enabled').length }} enabled · 1 disabled</span></div></div><div class="registry-summary__divider" /><div class="registry-summary__item"><span class="registry-summary__icon registry-summary__icon--blue"><AppIcon name="terminal" :size="18" /></span><div><strong>{{ tools.length }} 个 Tool</strong><span>4 个可供 Root + Subagents 使用</span></div></div><div class="registry-summary__divider" /><div class="registry-summary__item"><span class="registry-summary__icon registry-summary__icon--green"><AppIcon name="check" :size="18" /></span><div><strong>连接健康</strong><span>4 connected · 1 degraded</span></div></div></div>

    <section class="panel registry-panel">
      <div class="registry-tabs" role="tablist" aria-label="能力类型"><button type="button" role="tab" :aria-selected="activeTab === 'skills'" class="registry-tab" :class="{ 'registry-tab--active': activeTab === 'skills' }" @click="activeTab = 'skills'"><AppIcon name="spark" :size="16" />技能注册表 <span>{{ skills.length }}</span></button><button type="button" role="tab" :aria-selected="activeTab === 'tools'" class="registry-tab" :class="{ 'registry-tab--active': activeTab === 'tools' }" @click="activeTab = 'tools'"><AppIcon name="terminal" :size="16" />工具 contracts <span>{{ tools.length }}</span></button></div>
      <div v-if="activeTab === 'skills'" class="table-wrap">
        <table class="data-table registry-table"><thead><tr><th>Skill</th><th>描述</th><th>版本</th><th>分配范围</th><th>状态</th><th>最近更新</th><th /></tr></thead><tbody><tr v-for="skill in skills" :key="skill.id"><td><div class="resource-name"><span class="resource-icon resource-icon--skill"><AppIcon name="spark" :size="14" /></span><div><strong>{{ skill.name }}</strong><small>{{ skill.id }}</small></div></div></td><td class="description-cell">{{ skill.description }}</td><td><span class="version-pill">{{ skill.version }}</span></td><td class="muted-cell">{{ skill.assignedTodos }}</td><td><StatusBadge :label="skill.status === 'core' ? 'Core' : skill.status === 'enabled' ? 'Enabled' : 'Disabled'" :tone="skill.status === 'disabled' ? 'neutral' : skill.status === 'core' ? 'info' : 'success'" /></td><td class="muted-cell">{{ skill.updatedAt }}</td><td><button type="button" class="icon-button icon-button--small" :aria-label="`打开 ${skill.name}`"><AppIcon name="chevron" :size="15" /></button></td></tr></tbody></table>
      </div>
      <div v-else class="table-wrap">
        <table class="data-table registry-table"><thead><tr><th>Tool</th><th>描述</th><th>可用范围</th><th>Contract</th><th>连接</th><th>延迟</th><th /></tr></thead><tbody><tr v-for="tool in tools" :key="tool.id"><td><div class="resource-name"><span class="resource-icon resource-icon--tool"><AppIcon name="terminal" :size="14" /></span><div><strong>{{ tool.name }}</strong><small>{{ tool.id }}</small></div></div></td><td class="description-cell">{{ tool.description }}</td><td class="muted-cell">{{ tool.scope }}</td><td><span class="version-pill">{{ tool.contract }}</span></td><td><StatusBadge :label="testedTool === tool.id ? '测试中…' : tool.connection === 'connected' ? 'Connected' : tool.connection === 'degraded' ? 'Degraded' : 'Untested'" :tone="testedTool === tool.id ? 'info' : connectionTone(tool.connection)" /></td><td class="mono-cell">{{ tool.latency }}</td><td><button type="button" class="text-button text-button--compact" @click="testConnection(tool.id)">{{ testedTool === tool.id ? '运行中' : '测试连接' }}</button></td></tr></tbody></table>
      </div>
    </section>

    <div class="registry-footnote"><AppIcon name="shield" :size="15" /><span>所有 Tool result 都必须通过 typed contract 校验并绑定 evidence_id，原始大输出存放在 sandbox artifact。</span><a href="#" @click.prevent>查看 contract 规范 <AppIcon name="external" :size="12" /></a></div>
  </div>
</template>
