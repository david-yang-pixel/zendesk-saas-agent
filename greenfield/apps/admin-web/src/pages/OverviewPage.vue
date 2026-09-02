<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import type { OverviewData, RunStatus, StatusTone } from '../types'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

const placeholderOverview: OverviewData = {
  desiredState: 'running',
  configRevision: '—',
  heartbeat: null,
  metrics: [
    { label: '今日处理工单', value: '—', detail: '加载中', icon: 'activity', accent: 'blue' },
    { label: '当前队列', value: '—', detail: '加载中', icon: 'clock', accent: 'green' },
    { label: '成功完成率', value: '—', detail: '加载中', icon: 'check', accent: 'violet' },
    { label: '需关注', value: '—', detail: '加载中', icon: 'alert', accent: 'amber' },
  ],
  queueHealth: [],
  failures: [],
  recentRuns: [],
}

const overview = ref<OverviewData>(placeholderOverview)
const configSnapshot = ref<Awaited<ReturnType<typeof api.getConfig>> | null>(null)
const isRefreshing = ref(false)
const lastUpdated = ref('刚刚更新')
const errorMessage = ref('')

const isPaused = computed(() => overview.value.desiredState === 'paused')
const agentLabel = computed(() => (isPaused.value ? '已暂停' : '运行中'))
const agentTone = computed<StatusTone>(() => (isPaused.value ? 'warning' : 'success'))
const activeQueueTotal = computed(() => overview.value.queueHealth.reduce((total, item) => total + item.count, 0))

function queueWidth(item: { count: number }): string {
  if (!activeQueueTotal.value) return '0%'
  return `${Math.round((item.count / activeQueueTotal.value) * 100)}%`
}

async function refreshOverview() {
  isRefreshing.value = true
  errorMessage.value = ''
  try {
    const [snapshot, nextOverview] = await Promise.all([api.getConfig(), api.getOverview()])
    configSnapshot.value = snapshot
    overview.value = nextOverview
    lastUpdated.value = '刚刚更新'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取管理 API 失败'
  } finally {
    isRefreshing.value = false
  }
}

async function toggleAgent() {
  const desiredState = isPaused.value ? 'running' : 'paused'
  overview.value = { ...overview.value, desiredState }
  const snapshot = configSnapshot.value
  if (!snapshot) {
    await refreshOverview()
    return
  }
  errorMessage.value = ''
  try {
    const saved = await api.saveConfig({
      expectedRevision: snapshot.revision,
      config: { ...snapshot.config, desiredState },
    })
    configSnapshot.value = saved
    overview.value = { ...overview.value, configRevision: saved.revision }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存 Agent 状态失败'
    await refreshOverview()
    if (isConflict(error)) errorMessage.value = '配置版本已过期，已重新加载当前状态。请稍后重试。'
  }
}

function statusTone(status: RunStatus): StatusTone {
  if (status === 'completed') return 'success'
  if (status === 'running') return 'info'
  if (status === 'failed') return 'danger'
  return 'warning'
}

function isConflict(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error && (error as { status?: unknown }).status === 409
}

onMounted(() => { void refreshOverview() })
</script>

<template>
  <div class="page-view overview-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">OPERATIONS / LIVE SNAPSHOT</p>
        <h1>运行总览</h1>
        <p class="page-description">监控 ZendeskCasework Harness 的实时运行状态、队列与近期异常。</p>
      </div>
      <div class="heading-actions">
        <span class="last-updated"><span class="live-dot" aria-hidden="true" />{{ lastUpdated }}</span>
        <button type="button" class="button button--secondary" :disabled="isRefreshing" @click="refreshOverview">
          <AppIcon name="refresh" :size="15" :class="{ spinning: isRefreshing }" />
          {{ isRefreshing ? '刷新中…' : '刷新数据' }}
        </button>
      </div>
    </div>

    <div v-if="errorMessage" class="notice-banner notice-banner--danger" role="alert"><AppIcon name="alert" :size="18" /><div><strong>管理 API 暂时不可用</strong><span>{{ errorMessage }}</span></div></div>

    <section class="agent-banner" aria-labelledby="agent-state-title">
      <div class="agent-banner__identity">
        <div class="agent-emblem"><AppIcon name="spark" :size="21" /></div>
        <div>
          <div class="agent-title-row">
            <h2 id="agent-state-title">ZendeskCasework Agent</h2>
            <span data-testid="agent-status"><StatusBadge :label="agentLabel" :tone="agentTone" /></span>
          </div>
          <p>接收符合规则的工单，并在独立沙箱中执行调查与回复流程。</p>
        </div>
      </div>
      <div class="agent-banner__meta">
        <div><span>当前版本</span><strong>Harness {{ overview.configRevision }}</strong></div>
        <div><span>心跳</span><strong>{{ overview.heartbeat ?? '暂无数据' }}</strong></div>
        <button
          type="button"
          class="button"
          :class="isPaused ? 'button--success' : 'button--danger-soft'"
          data-testid="agent-toggle"
          :aria-pressed="isPaused"
          @click="toggleAgent"
        >
          <AppIcon :name="isPaused ? 'play' : 'pause'" :size="14" />
          {{ isPaused ? '恢复运行' : '暂停接收' }}
        </button>
      </div>
    </section>

    <div class="metric-grid">
      <MetricCard v-for="metric in overview.metrics" :key="metric.label" :metric="metric" />
    </div>

    <div class="content-grid content-grid--overview">
      <section class="panel queue-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">QUEUE HEALTH</p><h2>队列健康度</h2></div>
          <RouterLink to="/runs" class="text-button">查看全部 <AppIcon name="external" :size="13" /></RouterLink>
        </div>
        <div class="queue-summary">
          <div class="queue-total"><strong>{{ activeQueueTotal }}</strong><span>个活动任务</span></div>
          <div class="queue-track" role="img" aria-label="队列任务分布">
            <span v-for="(item, index) in overview.queueHealth" :key="item.label" class="queue-segment" :class="`queue-segment--${['blue', 'green', 'amber', 'red'][index % 4]}`" :style="{ width: queueWidth(item) }" />
          </div>
        </div>
        <div class="queue-list">
          <div v-for="item in overview.queueHealth" :key="item.label" class="queue-row">
            <div class="queue-row__label"><span class="queue-marker" :class="`marker-${item.tone}`" />{{ item.label }}</div>
            <div class="queue-row__description">{{ item.description }}</div>
            <strong>{{ item.count }}</strong>
          </div>
          <div v-if="overview.queueHealth.length === 0" class="empty-state" role="status">
            <AppIcon name="check" :size="17" />暂无活动队列数据
          </div>
        </div>
      </section>

      <section class="panel failure-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">ATTENTION NEEDED</p><h2>近期异常</h2></div>
          <span class="panel-count">{{ overview.failures.length }}</span>
        </div>
        <div class="failure-list">
          <article v-for="failure in overview.failures" :key="failure.id" class="failure-row">
            <div class="failure-icon" :class="`failure-icon--${failure.tone}`"><AppIcon :name="failure.tone === 'danger' ? 'alert' : 'clock'" :size="15" /></div>
            <div class="failure-copy"><strong>{{ failure.title }}</strong><span>{{ failure.detail }}</span></div>
            <time>{{ failure.time }}</time>
          </article>
          <div v-if="overview.failures.length === 0" class="empty-state" role="status">
            <AppIcon name="check" :size="17" />当前没有需要关注的异常
          </div>
        </div>
      </section>
    </div>

    <section class="panel recent-panel">
      <div class="panel-heading">
        <div><p class="eyebrow">RECENT RUNS</p><h2>近期运行</h2></div>
        <RouterLink to="/runs" class="text-button">进入运行记录 <AppIcon name="external" :size="13" /></RouterLink>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>工单</th><th>主题</th><th>状态</th><th>开始时间</th><th>耗时</th><th>Tool steps</th></tr></thead>
          <tbody>
            <tr v-for="run in overview.recentRuns" :key="`${run.ticketId}-${run.startedAt}`">
              <td><RouterLink to="/runs" class="ticket-link">{{ `#${run.ticketId}` }}</RouterLink></td>
              <td class="subject-cell">{{ run.subject }}</td>
              <td><StatusBadge :label="run.statusLabel" :tone="statusTone(run.status)" /></td>
              <td class="muted-cell">{{ run.startedAt }}</td>
              <td class="mono-cell">{{ run.duration }}</td>
              <td class="mono-cell">{{ run.steps ?? '未知' }}</td>
            </tr>
            <tr v-if="overview.recentRuns.length === 0"><td colspan="6" class="empty-cell"><AppIcon name="activity" :size="20" /><strong>暂无运行记录</strong><span>符合规则的工单进入 Harness 后会显示在这里。</span></td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
