<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import type { RunRecord, RunStatus, StatusTone } from '../types'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

const query = ref('')
const statusFilter = ref<'all' | RunStatus>('all')
const selectedRun = ref<RunRecord | null>(null)
const runRecords = ref<RunRecord[]>([])
const loading = ref(true)
const errorMessage = ref('')

async function loadRuns() {
  loading.value = true
  errorMessage.value = ''
  try {
    runRecords.value = (await api.getRuns()).items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取运行记录失败'
  } finally {
    loading.value = false
  }
}

const filteredRuns = computed(() => {
  const normalizedQuery = query.value.trim().toLowerCase()
  return runRecords.value.filter((run) => {
    const matchesStatus = statusFilter.value === 'all' || run.status === statusFilter.value
    const matchesQuery = !normalizedQuery || String(run.ticketId).includes(normalizedQuery) || run.subject.toLowerCase().includes(normalizedQuery) || run.sessionId.toLowerCase().includes(normalizedQuery)
    return matchesStatus && matchesQuery
  })
})

function statusTone(status: RunStatus): StatusTone {
  if (status === 'completed') return 'success'
  if (status === 'running') return 'info'
  if (status === 'failed') return 'danger'
  return 'warning'
}

function todoSummary(run: RunRecord): string {
  return run.todoCompleted === null || run.todoTotal === null
    ? '未知（尚未持久化）'
    : `${run.todoCompleted} / ${run.todoTotal}`
}

function todoWidth(run: RunRecord): string {
  if (run.todoCompleted === null || run.todoTotal === null || run.todoTotal <= 0) return '0%'
  return `${Math.min(100, Math.round((run.todoCompleted / run.todoTotal) * 100))}%`
}

function knownCount(value: number | null, suffix: string): string {
  return value === null ? '未知（尚未持久化）' : `${value}${suffix}`
}

const activeRuns = computed(() => runRecords.value.filter((run) => run.status === 'running').length)
const completedRuns = computed(() => runRecords.value.filter((run) => run.status === 'completed').length)
const awaitingRuns = computed(() => runRecords.value.filter((run) => run.status === 'awaiting_customer').length)
const failedRuns = computed(() => runRecords.value.filter((run) => run.status === 'failed').length)

function selectRun(run: RunRecord) {
  selectedRun.value = run
}

function closeDetails() {
  selectedRun.value = null
}

onMounted(() => { void loadRuns() })
</script>

<template>
  <div class="page-view runs-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">OBSERVABILITY / CASEWORK RUNS</p>
        <h1>工单运行</h1>
        <p class="page-description">按工单追踪会话、Todo、Tool Calls、Subagents、证据与沙箱生命周期，集中查看运行记录。</p>
      </div>
      <div class="heading-actions"><span class="last-updated"><span class="live-dot" aria-hidden="true" />{{ loading ? '读取中…' : '刚刚更新' }}</span><button type="button" class="button button--secondary" disabled><AppIcon name="download" :size="15" />导出记录</button></div>
    </div>

    <div v-if="errorMessage" class="notice-banner notice-banner--danger" role="alert"><AppIcon name="alert" :size="18" /><div><strong>运行记录不可用</strong><span>{{ errorMessage }}</span></div></div>

    <div class="run-metrics"><div><span class="run-metric-dot run-metric-dot--blue" /><strong>{{ activeRuns }}</strong><small>活动运行</small></div><div><span class="run-metric-dot run-metric-dot--green" /><strong>{{ completedRuns }}</strong><small>当前返回完成</small></div><div><span class="run-metric-dot run-metric-dot--amber" /><strong>{{ awaitingRuns }}</strong><small>等待客户</small></div><div><span class="run-metric-dot run-metric-dot--red" /><strong>{{ failedRuns }}</strong><small>需复核</small></div></div>

    <section class="panel runs-panel">
      <div class="runs-toolbar"><div class="search-field"><AppIcon name="search" :size="16" /><input v-model="query" data-testid="run-search" type="search" placeholder="搜索工单号、主题或 session ID…" aria-label="搜索运行记录"><button v-if="query" type="button" aria-label="清空搜索" @click="query = ''">×</button></div><div class="filter-group" aria-label="运行状态筛选"><button type="button" class="filter-button" :class="{ 'filter-button--active': statusFilter === 'all' }" @click="statusFilter = 'all'">全部 <span>{{ runRecords.length }}</span></button><button type="button" class="filter-button" :class="{ 'filter-button--active': statusFilter === 'running' }" @click="statusFilter = 'running'">运行中</button><button type="button" class="filter-button" :class="{ 'filter-button--active': statusFilter === 'awaiting_customer' }" @click="statusFilter = 'awaiting_customer'">等待客户</button><button type="button" class="filter-button" :class="{ 'filter-button--active': statusFilter === 'failed' }" @click="statusFilter = 'failed'">失败</button></div><button type="button" class="icon-button" aria-label="更多筛选"><AppIcon name="sliders" :size="17" /></button></div>
      <div class="table-wrap"><table class="data-table runs-table"><thead><tr><th>工单</th><th>主题 / 活动问题</th><th>状态</th><th>Todo</th><th>Tool calls</th><th>Evidence</th><th>开始时间</th><th>沙箱</th><th /></tr></thead><tbody><tr v-for="run in filteredRuns" :key="run.sessionId" class="run-table-row" :class="{ 'run-table-row--selected': selectedRun?.sessionId === run.sessionId }" @click="selectRun(run)"><td><a href="#" class="ticket-link" @click.stop.prevent="selectRun(run)">{{ `#${run.ticketId}` }}</a><small class="table-subtext">{{ run.sessionId }}</small></td><td><strong class="run-subject">{{ run.subject }}</strong><span class="table-subtext run-question">{{ run.question ?? '未知（尚未持久化）' }}</span></td><td><StatusBadge :label="run.statusLabel" :tone="statusTone(run.status)" /></td><td><span class="progress-value">{{ todoSummary(run) }}</span><span v-if="run.todoCompleted !== null && run.todoTotal !== null" class="mini-progress"><span :style="{ width: todoWidth(run) }" /></span></td><td class="mono-cell">{{ knownCount(run.toolCalls, '') }}</td><td class="mono-cell">{{ knownCount(run.evidence, '') }}</td><td class="muted-cell">{{ run.startedAt }}</td><td><StatusBadge :label="run.sandbox === 'running' ? 'Running' : 'Destroyed'" :tone="run.sandbox === 'running' ? 'info' : 'neutral'" /></td><td><AppIcon name="chevron" :size="15" class="row-chevron" /></td></tr><tr v-if="filteredRuns.length === 0"><td colspan="9" class="empty-cell"><AppIcon name="search" :size="22" /><strong>没有匹配的运行记录</strong><span>尝试修改搜索词或筛选条件。</span></td></tr></tbody></table></div>
    </section>

    <Transition name="drawer"><aside v-if="selectedRun" class="run-drawer" aria-label="运行详情"><div class="drawer-header"><div><p class="eyebrow">RUN DETAIL</p><h2>Ticket #{{ selectedRun.ticketId }}</h2></div><button type="button" class="icon-button" aria-label="关闭运行详情" @click="closeDetails">×</button></div><div class="drawer-subject"><StatusBadge :label="selectedRun.statusLabel" :tone="statusTone(selectedRun.status)" /><h3>{{ selectedRun.subject }}</h3><p>{{ selectedRun.question ?? '未知（尚未持久化）' }}</p></div><div class="drawer-section"><span class="drawer-label">RUN METADATA</span><dl class="detail-list"><div><dt>Session</dt><dd class="mono-cell">{{ selectedRun.sessionId }}</dd></div><div><dt>Config revision</dt><dd class="mono-cell">{{ selectedRun.configRevision ?? '未知（尚未持久化）' }}</dd></div><div><dt>Outcome</dt><dd>{{ selectedRun.outcome }}</dd></div><div><dt>Checkpoint</dt><dd class="mono-cell">{{ selectedRun.checkpoint ?? '未知（尚未持久化）' }}</dd></div><div><dt>Sandbox</dt><dd>{{ selectedRun.sandbox === 'running' ? '运行中' : '已销毁' }}</dd></div></dl></div><div class="drawer-section"><span class="drawer-label">ACTIVITY</span><div class="activity-list"><div><span class="activity-icon activity-icon--done"><AppIcon name="check" :size="13" /></span><span><strong>{{ selectedRun.evidence === null ? 'Evidence 未持久化' : 'Evidence 已记录' }}</strong><small>{{ knownCount(selectedRun.evidence, ' 个 typed evidence') }}</small></span></div><div><span class="activity-icon activity-icon--info"><AppIcon name="terminal" :size="13" /></span><span><strong>Tool Loop</strong><small>{{ knownCount(selectedRun.toolCalls, ' 次 Tool call') }} · {{ selectedRun.todoCompleted === null || selectedRun.todoTotal === null ? 'Todo 未持久化' : `${selectedRun.todoCompleted} / ${selectedRun.todoTotal} 个 Todo 完成` }}</small></span></div><div><span class="activity-icon activity-icon--info"><AppIcon name="spark" :size="13" /></span><span><strong>Subagents</strong><small>{{ knownCount(selectedRun.subagents, ' 个 Subagent') }}</small></span></div><div><span class="activity-icon activity-icon--neutral"><AppIcon name="box" :size="13" /></span><span><strong>Sandbox lifecycle</strong><small>{{ selectedRun.sandbox === 'running' ? 'creating → ready → running' : 'creating → ready → destroying → destroyed' }}</small></span></div></div></div><button type="button" class="button button--secondary drawer-action"><AppIcon name="external" :size="14" />查看完整事件时间线</button></aside></Transition>
  </div>
</template>
