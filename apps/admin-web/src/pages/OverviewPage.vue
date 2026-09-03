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
    { label: '已持久化总作业', value: '—', detail: '加载中', icon: 'database', accent: 'blue' },
    { label: '活动中 / 待处理', value: '—', detail: '加载中', icon: 'activity', accent: 'green' },
    { label: '等待客户答复', value: '—', detail: '加载中', icon: 'clock', accent: 'amber' },
    { label: '已拦截或异常', value: '—', detail: '加载中', icon: 'alert', accent: 'violet' },
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

function formatMetricLabel(label: string): string {
  if (label === 'Durable jobs') return '已持久化总作业'
  if (label === 'Active') return '活动中 / 待处理'
  if (label === 'Awaiting customer') return '等待客户答复'
  if (label === 'Failed') return '已拦截或异常'
  return label
}

function formatMetricDetail(detail: string): string {
  if (detail === 'Latest persisted jobs') return 'PostgreSQL 持久化队列总记录'
  if (detail === 'Currently claimable or leased') return '当前可领取或执行中的任务'
  if (detail === 'Clarification requested') return '已向客户发起反问或等待追问'
  if (detail === 'Requires operator review') return '触发模式 B 拦截或执行异常'
  return detail
}

const displayMetrics = computed(() => {
  return overview.value.metrics.map((m) => ({
    ...m,
    label: formatMetricLabel(m.label),
    detail: formatMetricDetail(m.detail),
  }))
})

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
  <div class="overview-page">
    <div class="overview-container">
      <!-- Header -->
      <header class="page-heading">
        <div class="heading-left">
          <div class="eyebrow-tag">OPERATIONS / LIVE SNAPSHOT</div>
          <h1>运行总览</h1>
          <p class="page-description">监控 ZendeskCasework Harness 的实时运行状态、队列与近期异常。</p>
        </div>
        <div class="heading-actions">
          <span class="last-updated">
            <span class="live-dot" />
            <span>{{ lastUpdated }}</span>
          </span>
          <button type="button" class="btn-refresh" :disabled="isRefreshing" @click="refreshOverview">
            <AppIcon name="refresh" :size="14" :class="{ spinning: isRefreshing }" />
            <span>{{ isRefreshing ? '刷新中…' : '刷新数据' }}</span>
          </button>
        </div>
      </header>

      <!-- Error Notice -->
      <div v-if="errorMessage" class="notice-banner notice-banner--danger" role="alert">
        <AppIcon name="alert" :size="18" />
        <div>
          <strong>管理 API 暂时不可用</strong>
          <span>{{ errorMessage }}</span>
        </div>
      </div>

      <!-- Agent Status Banner -->
      <section class="agent-banner" aria-labelledby="agent-state-title">
        <div class="agent-banner__identity">
          <div class="agent-emblem">
            <AppIcon name="spark" :size="22" />
          </div>
          <div class="agent-title-col">
            <div class="agent-title-row">
              <h2 id="agent-state-title">ZendeskCasework Agent</h2>
              <span data-testid="agent-status"><StatusBadge :label="agentLabel" :tone="agentTone" /></span>
            </div>
            <p>接收符合规则的工单，并在独立沙箱中执行自动化调查与回复流程。</p>
          </div>
        </div>
        <div class="agent-banner__meta">
          <div class="meta-chip">
            <span class="meta-chip__label">当前版本</span>
            <code class="meta-chip__val">Harness {{ overview.configRevision.slice(0, 12) }}</code>
          </div>
          <div class="meta-chip">
            <span class="meta-chip__label">运行时心跳</span>
            <span class="meta-chip__val">{{ overview.heartbeat ? '正常在线' : '运行正常' }}</span>
          </div>
          <button
            type="button"
            class="btn-agent-toggle"
            :class="isPaused ? 'btn-agent-toggle--resume' : 'btn-agent-toggle--pause'"
            data-testid="agent-toggle"
            :aria-pressed="isPaused"
            @click="toggleAgent"
          >
            <AppIcon :name="isPaused ? 'play' : 'pause'" :size="13" />
            <span>{{ isPaused ? '恢复运行' : '暂停接收' }}</span>
          </button>
        </div>
      </section>

      <!-- 4-Column KPI Metric Cards -->
      <section class="metric-grid">
        <MetricCard v-for="metric in displayMetrics" :key="metric.label" :metric="metric" />
      </section>

      <!-- Middle 2-Column Split: Queue Health & Failures -->
      <div class="content-grid--overview">
        <!-- Panel 1: Queue Health -->
        <section class="apple-panel queue-panel">
          <div class="panel-heading">
            <div>
              <span class="panel-tag">Queue Health</span>
              <h2>队列健康度</h2>
            </div>
            <RouterLink to="/runs" class="text-button">
              <span>查看全部</span>
              <AppIcon name="external" :size="12" />
            </RouterLink>
          </div>
          <div class="queue-summary">
            <div class="queue-total">
              <strong>{{ activeQueueTotal }}</strong>
              <span>个活动任务</span>
            </div>
            <div class="queue-track" role="img" aria-label="队列任务分布">
              <span
                v-for="(item, index) in overview.queueHealth"
                :key="item.label"
                class="queue-segment"
                :class="`queue-segment--${['blue', 'green', 'amber', 'red'][index % 4]}`"
                :style="{ width: queueWidth(item) }"
              />
            </div>
          </div>
          <div class="queue-list">
            <div v-for="item in overview.queueHealth" :key="item.label" class="queue-row">
              <div class="queue-row__label">
                <span class="queue-marker" :class="`marker-${item.tone}`" />
                <span>{{ item.label }}</span>
              </div>
              <div class="queue-row__description">{{ item.description }}</div>
              <strong class="queue-row__count">{{ item.count }}</strong>
            </div>
            <div v-if="overview.queueHealth.length === 0" class="empty-state" role="status">
              <AppIcon name="check" :size="16" />
              <span>当前队列状态健康，暂无积压</span>
            </div>
          </div>
        </section>

        <!-- Panel 2: Failures / Attention -->
        <section class="apple-panel failure-panel">
          <div class="panel-heading">
            <div>
              <span class="panel-tag">Attention Needed</span>
              <h2>近期异常与拦截</h2>
            </div>
            <span class="panel-count">{{ overview.failures.length }}</span>
          </div>
          <div class="failure-list">
            <article v-for="failure in overview.failures" :key="failure.id" class="failure-row">
              <div class="failure-icon" :class="`failure-icon--${failure.tone}`">
                <AppIcon :name="failure.tone === 'danger' ? 'alert' : 'clock'" :size="14" />
              </div>
              <div class="failure-copy">
                <strong>{{ failure.title }}</strong>
                <span>{{ failure.detail }}</span>
              </div>
              <time class="failure-time">{{ failure.time }}</time>
            </article>
            <div v-if="overview.failures.length === 0" class="empty-state" role="status">
              <AppIcon name="check" :size="16" />
              <span>当前系统稳定运行，无未处理异常</span>
            </div>
          </div>
        </section>
      </div>

      <!-- Bottom Panel: Recent Runs -->
      <section class="apple-panel recent-panel">
        <div class="panel-heading">
          <div>
            <span class="panel-tag">Recent Executions</span>
            <h2>近期运行审计</h2>
          </div>
          <RouterLink to="/runs" class="text-button">
            <span>进入运行回放</span>
            <AppIcon name="external" :size="12" />
          </RouterLink>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>工单</th>
                <th>提问内容 / 主题</th>
                <th>状态</th>
                <th>开始时间</th>
                <th>耗时</th>
                <th>Tool Steps</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in overview.recentRuns" :key="`${run.ticketId}-${run.startedAt}`">
                <td>
                  <RouterLink to="/runs" class="ticket-link">{{ `#${run.ticketId}` }}</RouterLink>
                </td>
                <td class="subject-cell">{{ run.subject }}</td>
                <td>
                  <StatusBadge :label="run.statusLabel" :tone="statusTone(run.status)" />
                </td>
                <td class="muted-cell">{{ run.startedAt }}</td>
                <td class="mono-cell">{{ run.duration }}</td>
                <td class="mono-cell">
                  <span class="step-pill">{{ run.steps ?? '0 步' }}</span>
                </td>
              </tr>
              <tr v-if="overview.recentRuns.length === 0">
                <td colspan="6" class="empty-cell">
                  <AppIcon name="activity" :size="20" />
                  <strong>暂无运行记录</strong>
                  <span>符合规则的工单进入系统推理后将在此展示。</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.overview-page {
  min-height: 100%;
  background: #fbfbfd;
  color: #1d1d1f;
  padding: 28px 40px;
  overflow-y: auto;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
}

.overview-container {
  max-width: 1160px;
  margin: 0 auto;
}

/* Page Heading */
.page-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  padding-bottom: 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  margin-bottom: 24px;
}

.eyebrow-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #0071e3;
  background: rgba(0, 113, 227, 0.08);
  padding: 3px 8px;
  border-radius: 6px;
  margin-bottom: 8px;
}

.heading-left h1 {
  margin: 0 0 4px;
  font-size: 24px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -0.02em;
}

.page-description {
  margin: 0;
  color: #86868b;
  font-size: 13px;
}

.heading-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.last-updated {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #86868b;
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #34c759;
  box-shadow: 0 0 0 2px rgba(52, 199, 89, 0.2);
}

.btn-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #ffffff;
  color: #1d1d1f;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.btn-refresh:hover {
  background: #f5f5f7;
  border-color: rgba(0, 0, 0, 0.2);
}

/* Agent Banner */
.agent-banner {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 14px;
  padding: 18px 24px;
  margin-bottom: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.agent-banner__identity {
  display: flex;
  align-items: center;
  gap: 16px;
}

.agent-emblem {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.12), rgba(175, 82, 222, 0.12));
  color: #0071e3;
  display: grid;
  place-items: center;
}

.agent-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.agent-title-row h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1d1d1f;
}

.agent-title-col p {
  margin: 4px 0 0;
  font-size: 12px;
  color: #86868b;
}

.agent-banner__meta {
  display: flex;
  align-items: center;
  gap: 20px;
}

.meta-chip {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: right;
}
.meta-chip__label {
  font-size: 10px;
  font-weight: 600;
  color: #86868b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.meta-chip__val {
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
  font-family: "SF Mono", Menlo, monospace;
}

.btn-agent-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 14px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-agent-toggle--pause {
  background: rgba(255, 59, 48, 0.1);
  color: #ff3b30;
}
.btn-agent-toggle--pause:hover {
  background: rgba(255, 59, 48, 0.18);
}
.btn-agent-toggle--resume {
  background: rgba(52, 199, 89, 0.12);
  color: #2e7d32;
}
.btn-agent-toggle--resume:hover {
  background: rgba(52, 199, 89, 0.2);
}

/* 4-Column Metric Grid */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

/* Middle Split Content */
.content-grid--overview {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 24px;
}

.apple-panel {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 14px;
  padding: 20px 22px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.panel-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.panel-tag {
  display: inline-block;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #86868b;
  margin-bottom: 4px;
}

.panel-heading h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1d1d1f;
}

.text-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  color: #0071e3;
  transition: opacity 0.15s;
}
.text-button:hover { opacity: 0.8; }

.panel-count {
  font-size: 11px;
  font-weight: 600;
  background: rgba(255, 59, 48, 0.1);
  color: #ff3b30;
  padding: 2px 8px;
  border-radius: 10px;
}

/* Queue Visual Track */
.queue-summary {
  margin-bottom: 16px;
}
.queue-total {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 10px;
}
.queue-total strong {
  font-size: 24px;
  font-weight: 700;
  color: #1d1d1f;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
}
.queue-total span {
  font-size: 12px;
  color: #86868b;
}

.queue-track {
  height: 8px;
  background: #f0f0f2;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
}
.queue-segment--blue { background: #0071e3; }
.queue-segment--green { background: #34c759; }
.queue-segment--amber { background: #f5a623; }
.queue-segment--red { background: #ff3b30; }

.queue-list {
  display: flex;
  flex-direction: column;
}
.queue-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
  font-size: 12px;
}
.queue-row:last-child { border-bottom: none; }

.queue-row__label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  color: #1d1d1f;
}
.queue-marker {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.marker-info { background: #0071e3; }
.marker-success { background: #34c759; }
.marker-warning { background: #f5a623; }
.marker-danger { background: #ff3b30; }

.queue-row__description {
  color: #86868b;
  font-size: 11px;
}
.queue-row__count {
  font-family: "SF Mono", monospace;
  color: #1d1d1f;
}

/* Failure / Attention List */
.failure-list {
  display: flex;
  flex-direction: column;
}
.failure-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}
.failure-row:last-child { border-bottom: none; }

.failure-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.failure-icon--danger { background: rgba(255, 59, 48, 0.1); color: #ff3b30; }
.failure-icon--warning { background: rgba(245, 166, 35, 0.1); color: #d97706; }

.failure-copy {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.failure-copy strong {
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
}
.failure-copy span {
  font-size: 11px;
  color: #86868b;
}

.failure-time {
  font-size: 11px;
  color: #86868b;
  white-space: nowrap;
}

.empty-state {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px 0;
  color: #86868b;
  font-size: 12px;
  justify-content: center;
}

/* Recent Runs Table */
.recent-panel {
  padding: 20px 24px;
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 10px;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  text-align: left;
}

.data-table th {
  background: #f8f9fa;
  color: #6e6e73;
  font-weight: 600;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.data-table td {
  padding: 12px 14px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
  color: #1d1d1f;
  transition: background 0.1s;
}

.data-table tr:hover td {
  background: #f5f5f7;
}

.ticket-link {
  color: #0071e3;
  font-weight: 600;
  font-family: "SF Mono", monospace;
}

.subject-cell {
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}

.muted-cell {
  color: #86868b;
}

.mono-cell {
  font-family: "SF Mono", monospace;
  font-size: 11px;
}

.step-pill {
  background: #f0f0f2;
  color: #515154;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.empty-cell {
  text-align: center;
  padding: 36px !important;
  color: #86868b;
}
.empty-cell strong {
  display: block;
  margin: 6px 0 2px;
  color: #1d1d1f;
  font-size: 13px;
}
.empty-cell span {
  font-size: 12px;
}
</style>
