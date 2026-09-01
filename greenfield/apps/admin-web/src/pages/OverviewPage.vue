<script setup lang="ts">
import { computed, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { failures, metricCards, queueHealth, recentRuns } from '../data/mockData'
import type { RunStatus, StatusTone } from '../types'

const isPaused = ref(false)
const isRefreshing = ref(false)
const lastUpdated = ref('刚刚更新')

const agentLabel = computed(() => (isPaused.value ? '已暂停' : '运行中'))
const agentTone = computed<StatusTone>(() => (isPaused.value ? 'warning' : 'success'))

function refreshOverview() {
  isRefreshing.value = true
  window.setTimeout(() => {
    isRefreshing.value = false
    lastUpdated.value = '刚刚更新'
  }, 350)
}

function statusTone(status: RunStatus): StatusTone {
  if (status === 'completed') return 'success'
  if (status === 'running') return 'info'
  if (status === 'failed') return 'danger'
  return 'warning'
}
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
        <div><span>当前版本</span><strong>Harness r42</strong></div>
        <div><span>心跳</span><strong>18 秒前</strong></div>
        <button
          type="button"
          class="button"
          :class="isPaused ? 'button--success' : 'button--danger-soft'"
          data-testid="agent-toggle"
          :aria-pressed="isPaused"
          @click="isPaused = !isPaused"
        >
          <AppIcon :name="isPaused ? 'play' : 'pause'" :size="14" />
          {{ isPaused ? '恢复运行' : '暂停接收' }}
        </button>
      </div>
    </section>

    <div class="metric-grid">
      <MetricCard v-for="metric in metricCards" :key="metric.label" :metric="metric" />
    </div>

    <div class="content-grid content-grid--overview">
      <section class="panel queue-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">QUEUE HEALTH</p><h2>队列健康度</h2></div>
          <button type="button" class="text-button">查看全部 <AppIcon name="external" :size="13" /></button>
        </div>
        <div class="queue-summary">
          <div class="queue-total"><strong>14</strong><span>个活动任务</span></div>
          <div class="queue-track" aria-label="队列任务分布">
            <span class="queue-segment queue-segment--blue" style="width: 50%" />
            <span class="queue-segment queue-segment--green" style="width: 29%" />
            <span class="queue-segment queue-segment--amber" style="width: 14%" />
            <span class="queue-segment queue-segment--red" style="width: 7%" />
          </div>
        </div>
        <div class="queue-list">
          <div v-for="item in queueHealth" :key="item.label" class="queue-row">
            <div class="queue-row__label"><span class="queue-marker" :class="`marker-${item.tone}`" />{{ item.label }}</div>
            <div class="queue-row__description">{{ item.description }}</div>
            <strong>{{ item.count }}</strong>
          </div>
        </div>
      </section>

      <section class="panel failure-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">ATTENTION NEEDED</p><h2>近期异常</h2></div>
          <span class="panel-count">{{ failures.length }}</span>
        </div>
        <div class="failure-list">
          <article v-for="failure in failures" :key="failure.id" class="failure-row">
            <div class="failure-icon" :class="`failure-icon--${failure.tone}`"><AppIcon :name="failure.tone === 'danger' ? 'alert' : 'clock'" :size="15" /></div>
            <div class="failure-copy"><strong>{{ failure.title }}</strong><span>{{ failure.detail }}</span></div>
            <time>{{ failure.time }}</time>
          </article>
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
          <thead><tr><th>工单</th><th>主题</th><th>状态</th><th>开始时间</th><th>耗时</th><th>Tool steps</th><th /></tr></thead>
          <tbody>
            <tr v-for="run in recentRuns" :key="run.ticketId">
              <td><a href="#" class="ticket-link" @click.prevent>{{ `#${run.ticketId}` }}</a></td>
              <td class="subject-cell">{{ run.subject }}</td>
              <td><StatusBadge :label="run.statusLabel" :tone="statusTone(run.status)" /></td>
              <td class="muted-cell">{{ run.startedAt }}</td>
              <td class="mono-cell">{{ run.duration }}</td>
              <td class="mono-cell">{{ run.steps }}</td>
              <td><button type="button" class="icon-button icon-button--small" aria-label="打开运行详情"><AppIcon name="chevron" :size="15" /></button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
