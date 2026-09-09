<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import { useI18n } from '../composables/useI18n'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi
const { t, locale } = useI18n()

interface TraceItem {
  ticket_id: number
  job_id: string
  session_id?: string
  status: string
  created_at: string
  finished_at?: string
  duration: string
  customer_message: string
  requester_email: string
  model: string
  tool_calls_count: number
  reply_preview: string
  delivery_status?: string
  has_trace: boolean
}

interface TraceDetail {
  ticket_id: number
  job_id: string
  status: string
  created_at: string
  finished_at?: string
  duration: string
  customer_message: string
  requester_email: string
  model: string
  steps: Array<{
    type: 'thinking' | 'tool_call' | 'answer'
    thinking?: string
    toolCallId?: string
    call_id?: string
    toolName?: string
    service?: string
    arguments?: any
    result?: any
    status?: string
    evidence_ids?: string[]
    text?: string
  }>
  final_reply: string
  delivery?: {
    channel: string
    status: string
    total_duration_ms?: number
    timings?: Record<string, number>
  }
  error?: string
}

const query = ref('')
const statusFilter = ref<'all' | 'completed' | 'running' | 'failed'>('all')
const traceList = ref<TraceItem[]>([])
const selectedTicketId = ref<number | null>(null)
const selectedDetail = ref<TraceDetail | null>(null)
const loadingList = ref(false)
const loadingDetail = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')
const thinkingExpanded = ref(true)
const isProcessingLogExpanded = ref(false)

const activeTab = ref<'tickets' | 'console'>('tickets')
const liveLogs = ref<string[]>([])
const loadingLogs = ref(false)
const logFilter = ref('')
const logLevel = ref<'all' | 'error' | 'warn'>('all')
const autoRefreshLogs = ref(false)
let logTimer: ReturnType<typeof setInterval> | null = null

async function loadLiveLogs() {
  loadingLogs.value = true
  try {
    const res = await api.getLiveLogs(200)
    liveLogs.value = res.lines || []
  } catch (err: any) {
    liveLogs.value = [`[ERROR] 获取实时日志失败: ${err.message}`]
  } finally {
    loadingLogs.value = false
  }
}

watch(autoRefreshLogs, (enabled) => {
  if (enabled) {
    if (activeTab.value === 'console' && liveLogs.value.length === 0) {
      loadLiveLogs()
    }
    logTimer = setInterval(() => {
      if (activeTab.value === 'console') {
        loadLiveLogs()
      }
    }, 3000)
  } else if (logTimer) {
    clearInterval(logTimer)
    logTimer = null
  }
})

watch(activeTab, (tab) => {
  if (tab === 'console' && liveLogs.value.length === 0) {
    loadLiveLogs()
  }
})

onUnmounted(() => {
  if (logTimer) {
    clearInterval(logTimer)
    logTimer = null
  }
})

function getLineClass(line: string): string {
  if (line.includes('ERROR') || line.includes('error') || line.includes('Exception') || line.includes('Traceback') || line.includes('Failed')) return 'line-error'
  if (line.includes('WARN') || line.includes('warn')) return 'line-warn'
  if (line.includes('POST') || line.includes('GET') || line.includes('HTTP')) return 'line-http'
  if (line.includes('Worker') || line.includes('pi_worker') || line.includes('job_id')) return 'line-worker'
  return 'line-default'
}

const filteredLogs = computed(() => {
  let list = liveLogs.value
  if (logLevel.value === 'error') {
    list = list.filter((l) => l.includes('ERROR') || l.includes('error') || l.includes('Exception') || l.includes('Failed'))
  } else if (logLevel.value === 'warn') {
    list = list.filter((l) => l.includes('WARN') || l.includes('warn') || l.includes('ERROR') || l.includes('error'))
  }
  if (logFilter.value.trim()) {
    const kw = logFilter.value.trim().toLowerCase()
    list = list.filter((l) => l.toLowerCase().includes(kw))
  }
  return list
})

async function loadTraces() {
  loadingList.value = true
  errorMessage.value = ''
  try {
    const res = await api.getTraces({
      query: query.value,
      status: statusFilter.value,
    })
    traceList.value = res.items || []
    if (traceList.value.length > 0 && selectedTicketId.value === null) {
      selectTicket(traceList.value[0].ticket_id)
    }
  } catch (err: any) {
    errorMessage.value = err.message || '获取工单推演流水失败'
  } finally {
    loadingList.value = false
  }
}

async function selectTicket(ticketId: number) {
  selectedTicketId.value = ticketId
  loadingDetail.value = true
  selectedDetail.value = null
  isProcessingLogExpanded.value = false
  try {
    const detail = await api.getTrace(ticketId)
    selectedDetail.value = detail
  } catch (err: any) {
    console.error('Failed to load trace detail:', err)
  } finally {
    loadingDetail.value = false
  }
}

async function retryCurrentJob() {
  if (!selectedDetail.value) return
  const jobId = selectedDetail.value.job_id
  actionMessage.value = '正在重新入队...'
  try {
    await api.retryJob(jobId)
    actionMessage.value = '已重新入队！Pi Agent 即将重新推理。'
    await loadTraces()
    if (selectedTicketId.value) {
      await selectTicket(selectedTicketId.value)
    }
    setTimeout(() => { actionMessage.value = ''; }, 4000)
  } catch (err: any) {
    actionMessage.value = `重试失败: ${err.message}`
  }
}

async function skipCurrentJob(jobId?: string) {
  const targetId = jobId || selectedDetail.value?.job_id
  if (!targetId) return
  if (!window.confirm('确定要终止并跳过该工单吗？系统将释放租约并标记为已跳过，避免再次排队。')) return
  actionMessage.value = '正在标记跳过...'
  try {
    await api.skipJob(targetId)
    actionMessage.value = '已终止并跳过该工单！'
    await loadTraces()
    if (selectedTicketId.value) {
      await selectTicket(selectedTicketId.value)
    }
    setTimeout(() => { actionMessage.value = ''; }, 4000)
  } catch (err: any) {
    actionMessage.value = `跳过失败: ${err.message}`
  }
}

async function escalateCurrentJob(jobId?: string) {
  const targetId = jobId || selectedDetail.value?.job_id
  if (!targetId) return
  if (!window.confirm('确定要手动转交人工处理吗？系统将标记转人工并向飞书群推送告警卡片。')) return
  actionMessage.value = '正在转交人工...'
  try {
    await api.escalateJob(targetId)
    actionMessage.value = '已成功转交人工并触发飞书告警！'
    await loadTraces()
    if (selectedTicketId.value) {
      await selectTicket(selectedTicketId.value)
    }
    setTimeout(() => { actionMessage.value = ''; }, 4000)
  } catch (err: any) {
    actionMessage.value = `转交人工失败: ${err.message}`
  }
}

const expandedTools = ref<Record<number, boolean>>({})
const expandedTables = ref<Record<number, boolean>>({})
const copiedKey = ref('')

const toolCallSteps = computed(() => {
  if (!selectedDetail.value?.steps) return []
  return selectedDetail.value.steps.filter(s => s.type === 'tool_call' || s.toolName)
})

function isToolExpanded(idx: number): boolean {
  return expandedTools.value[idx] !== false
}

function toggleToolExpand(idx: number) {
  expandedTools.value[idx] = !isToolExpanded(idx)
}

function isTableExpanded(idx: number): boolean {
  return !!expandedTables.value[idx]
}

function toggleTableExpand(idx: number) {
  expandedTables.value[idx] = !isTableExpanded(idx)
}

function copyText(text: string, key: string) {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => {})
  }
  copiedKey.value = key
  setTimeout(() => {
    if (copiedKey.value === key) copiedKey.value = ''
  }, 2000)
}

function serviceBadgeClass(service?: string): string {
  if (service === 'db-gateway' || service === 'db') return 'badge-db'
  if (service === 'signoz') return 'badge-signoz'
  if (service === 'docs') return 'badge-docs'
  if (service === 'zendesk') return 'badge-zd'
  if (service === 'source') return 'badge-source'
  if (service === 'github') return 'badge-github'
  if (service === 'feishu') return 'badge-feishu'
  if (service?.includes('relay')) return 'badge-relay'
  return 'badge-default'
}

function isTabular(val: any): boolean {
  if (!Array.isArray(val) || val.length === 0) return false
  return typeof val[0] === 'object' && val[0] !== null
}

function getTableHeaders(rows: any[]): string[] {
  if (!Array.isArray(rows) || rows.length === 0) return []
  return Object.keys(rows[0])
}

onMounted(() => {
  loadTraces()
})
</script>

<template>
  <div class="runs-page-container">
    <!-- Sub Header with Segmented Control -->
    <header class="runs-nav-bar">
      <div class="nav-tabs">
        <button
          type="button"
          :class="['tab-btn', { active: activeTab === 'tickets' }]"
          @click="activeTab = 'tickets'"
        >
          <AppIcon name="runs" :size="14" />
          <span>{{ t('runs.tab.tickets') }}</span>
          <span class="tab-badge" v-if="traceList.length > 0">{{ traceList.length }}</span>
        </button>
        <button
          type="button"
          :class="['tab-btn', { active: activeTab === 'console' }]"
          @click="activeTab = 'console'"
        >
          <AppIcon name="terminal" :size="14" />
          <span>{{ t('runs.tab.console') }}</span>
          <span class="pulse-indicator"></span>
        </button>
      </div>
    </header>

    <!-- Tab 1: Ticket Traces Workspace -->
    <div v-show="activeTab === 'tickets'" class="trace-workspace">
      <!-- Left Column: Tickets Stream -->
      <aside class="trace-sidebar">
        <div class="trace-sidebar__header">
          <div class="header-title-row">
            <h3>{{ t('runs.sidebar.title') }}</h3>
            <button class="btn-icon" :title="t('runs.console.refresh_btn')" @click="loadTraces" :disabled="loadingList">
              <AppIcon name="refresh" :size="15" />
            </button>
          </div>
          <div class="search-box">
            <AppIcon name="search" :size="14" class="search-icon" />
            <input
              v-model="query"
              type="text"
              :placeholder="t('runs.search.placeholder')"
              @input="loadTraces"
            />
          </div>
          <div class="filter-pills">
            <button
              :class="['pill', { active: statusFilter === 'all' }]"
              @click="statusFilter = 'all'; loadTraces()"
            >{{ t('runs.filter.all') }}</button>
            <button
              :class="['pill', { active: statusFilter === 'completed' }]"
              @click="statusFilter = 'completed'; loadTraces()"
            >{{ t('runs.filter.completed') }}</button>
            <button
              :class="['pill', { active: statusFilter === 'running' }]"
              @click="statusFilter = 'running'; loadTraces()"
            >{{ t('runs.filter.running') }}</button>
            <button
              :class="['pill', { active: statusFilter === 'failed' }]"
              @click="statusFilter = 'failed'; loadTraces()"
            >{{ t('runs.filter.failed') }}</button>
          </div>
        </div>

        <div class="ticket-list" v-if="!loadingList && traceList.length > 0">
          <div
            v-for="item in traceList"
            :key="item.ticket_id"
            :class="['ticket-item', { active: selectedTicketId === item.ticket_id }]"
            @click="selectTicket(item.ticket_id)"
          >
            <div class="ticket-item__top">
              <span class="ticket-id">#{{ item.ticket_id }}</span>
              <span :class="['status-dot', item.status]"></span>
              <span class="status-tag">{{ item.status }}</span>
              <span class="duration-badge">{{ item.duration }}</span>
            </div>
            <div class="ticket-email" v-if="item.requester_email">{{ item.requester_email }}</div>
            <div class="ticket-preview">{{ item.customer_message || item.reply_preview || ('工单 #' + item.ticket_id) }}</div>
            <div class="ticket-meta">
              <span class="meta-tag" v-if="item.tool_calls_count > 0">🛠️ {{ item.tool_calls_count }} 工具</span>
              <span class="meta-tag" v-if="item.delivery_status">🚀 {{ item.delivery_status }}</span>
              <span class="meta-time">{{ item.created_at.slice(11, 19) }}</span>
            </div>
          </div>
        </div>

        <div class="empty-list" v-else-if="!loadingList">
          <AppIcon name="runs" :size="24" />
          <p>{{ t('runs.empty.tickets') }}</p>
        </div>

        <div class="loading-state" v-else>
          <p>{{ t('runs.loading.tickets') }}</p>
        </div>
      </aside>

      <!-- Right Column: Interactive Trace Player -->
      <main class="trace-player">
        <div v-if="actionMessage" class="action-banner">{{ actionMessage }}</div>

        <div v-if="selectedDetail" class="trace-content">
          <!-- Hero Header -->
          <header class="player-hero">
            <div class="hero-main">
              <div class="hero-title-line">
                <h2>工单 #{{ selectedDetail.ticket_id }} {{ t('runs.hero.audit') }}</h2>
                <span :class="['status-pill', selectedDetail.status]">{{ selectedDetail.status }}</span>
              </div>
              <div class="hero-sub">
                <span>{{ t('runs.hero.customer') }}: <strong>{{ selectedDetail.requester_email || '未知客户' }}</strong></span>
                <span class="divider">·</span>
                <span>{{ t('runs.hero.model') }}: <strong>{{ selectedDetail.model || 'gpt-5.6-terra' }}</strong></span>
                <span class="divider">·</span>
                <span>{{ t('runs.hero.duration') }}: <strong>{{ selectedDetail.duration }}</strong></span>
              </div>
            </div>
            <div class="hero-actions">
              <button class="btn-action" :title="t('runs.btn.retry')" @click="retryCurrentJob">
                <AppIcon name="refresh" :size="14" />
                <span>{{ t('runs.btn.retry') }}</span>
              </button>
              <button class="btn-action btn-skip" :title="t('runs.btn.skip')" @click="skipCurrentJob()">
                <span>{{ t('runs.btn.skip') }}</span>
              </button>
              <button class="btn-action btn-escalate" :title="t('runs.btn.escalate')" @click="escalateCurrentJob()">
                <span>{{ t('runs.btn.escalate') }}</span>
              </button>
              <a
                :href="'https://langgenius.zendesk.com/agent/tickets/' + selectedDetail.ticket_id"
                target="_blank"
                class="btn-action btn-secondary"
              >
                <span>{{ t('runs.btn.zd_origin') }}</span>
                <AppIcon name="external" :size="13" />
              </a>
            </div>
          </header>

          <!-- Timeline Stream -->
          <div class="timeline-stream">
            <!-- Step 1: Customer Incoming -->
            <div class="stream-node">
              <div class="node-marker marker-inbound">📥</div>
              <div class="node-body card">
                <div class="card-header">
                  <span class="step-title">{{ t('runs.step.customer') }}</span>
                  <span class="timestamp">{{ selectedDetail.created_at }}</span>
                </div>
                <div class="customer-message-box">
                  {{ selectedDetail.customer_message || '未获取到客户原始消息' }}
                </div>
              </div>
            </div>

            <!-- Step Notice (if error or cancelled protection) -->
            <div class="stream-node" v-if="selectedDetail.error">
              <div class="node-marker marker-notice">{{ selectedDetail.status === 'cancelled' ? '🛡️' : '⚠️' }}</div>
              <div :class="['node-body', 'card', 'card-notice', selectedDetail.status]">
                <div class="card-header">
                  <span class="step-title">{{ selectedDetail.status === 'cancelled' ? '模式 B：人工优先保护拦截' : '系统排障/执行状态提示' }}</span>
                  <span :class="['status-pill', selectedDetail.status]">{{ selectedDetail.status }}</span>
                </div>
                <div class="notice-box">
                  {{ selectedDetail.error }}
                </div>
              </div>
            </div>

            <!-- Step 2: Processing Details (Collapsible by default) -->
            <div class="stream-node processing-node" v-if="(selectedDetail.steps && selectedDetail.steps.length > 0) || !selectedDetail.error">
              <div class="node-marker marker-processing">⚡</div>
              <div class="node-body card card-processing-log">
                <!-- Clickable Collapsible Bar -->
                <div class="card-header cursor-pointer processing-header" @click="isProcessingLogExpanded = !isProcessingLogExpanded">
                  <div class="processing-meta">
                    <span class="processing-icon">
                      <AppIcon name="spark" :size="14" />
                    </span>
                    <strong class="processing-title">{{ t('runs.step.processing') }}</strong>
                    <span class="badge-tool-count" v-if="toolCallSteps.length > 0">
                      {{ toolCallSteps.length }} {{ t('runs.step.tools_count') }}
                    </span>
                    <span class="badge-tool-count badge-direct" v-else>
                      {{ t('runs.step.direct_resolve') }}
                    </span>
                    <span class="badge-thinking-tag" v-if="selectedDetail.steps && selectedDetail.steps.some(s => s.type === 'thinking')">
                      {{ locale === 'zh-CN' ? '含思考链' : 'Reasoning' }}
                    </span>
                    <span class="badge-duration" v-if="selectedDetail.duration">
                      {{ selectedDetail.duration }}
                    </span>
                  </div>
                  <div class="header-right">
                    <button type="button" class="btn-toggle-processing-log">
                      {{ isProcessingLogExpanded ? t('runs.step.processing_collapse') : t('runs.step.processing_expand') }}
                    </button>
                  </div>
                </div>

                <!-- Collapsible Body: Only visible when clicked -->
                <div class="processing-log-body" v-show="isProcessingLogExpanded">
                  <!-- Reasoning / Thinking -->
                  <div
                    class="inner-step-card card card-thinking"
                    v-for="(step, idx) in (selectedDetail.steps || []).filter(s => s.type === 'thinking')"
                    :key="'th-' + idx"
                  >
                    <div class="card-header thinking-toggle" @click="thinkingExpanded = !thinkingExpanded">
                      <div class="thinking-label">
                        <AppIcon name="spark" :size="14" />
                        <strong>{{ t('runs.step.thinking') }}</strong>
                      </div>
                      <button class="btn-text">{{ thinkingExpanded ? (locale === 'zh-CN' ? '折叠' : 'Collapse') : (locale === 'zh-CN' ? '展开' : 'Expand') }}</button>
                    </div>
                    <div class="thinking-content" v-if="thinkingExpanded">
                      <pre>{{ step.thinking }}</pre>
                    </div>
                  </div>

                  <!-- Tool Calls -->
                  <div
                    class="inner-step-card card card-tool"
                    v-for="(step, idx) in toolCallSteps"
                    :key="'tc-' + idx"
                  >
                    <div class="card-header cursor-pointer" @click="toggleToolExpand(idx)">
                      <div class="tool-meta">
                        <span :class="['service-badge', serviceBadgeClass(step.service)]">{{ step.service || 'MCP' }}</span>
                        <strong class="tool-name">{{ step.toolName }}</strong>
                        <span :class="['status-pill', step.status === 'failed' ? 'pill-fail' : 'pill-ok']">
                          {{ step.status === 'failed' ? 'Failed' : 'Succeeded' }}
                        </span>
                        <span v-if="step.call_id || step.toolCallId" class="call-id-tag">
                          #{{ step.call_id || step.toolCallId }}
                        </span>
                      </div>
                      <div class="header-right">
                        <span class="step-index">{{ t('runs.step.tools') }} #{{ idx + 1 }}</span>
                        <button type="button" class="btn-text tool-toggle-btn" @click.stop="toggleToolExpand(idx)">
                          {{ isToolExpanded(idx) ? (locale === 'zh-CN' ? '折叠' : 'Collapse') : (locale === 'zh-CN' ? '展开' : 'Expand') }}
                        </button>
                      </div>
                    </div>

                    <div class="tool-expanded-body" v-show="isToolExpanded(idx)">
                      <!-- Tool Arguments -->
                      <div class="code-block input-block">
                        <div class="code-header-bar">
                          <span class="code-label-title">
                            <AppIcon name="sliders" :size="12" />
                            {{ locale === 'zh-CN' ? '输入参数 (Arguments)' : 'Input Arguments' }}
                          </span>
                          <button
                            v-if="step.arguments && (typeof step.arguments === 'string' || Object.keys(step.arguments).length > 0)"
                            type="button"
                            class="btn-copy-sm"
                            @click="copyText(typeof step.arguments === 'string' ? step.arguments : (step.arguments?.query || JSON.stringify(step.arguments, null, 2)), 'arg-' + idx)"
                          >
                            {{ copiedKey === 'arg-' + idx ? (locale === 'zh-CN' ? '已复制 ✓' : 'Copied ✓') : (locale === 'zh-CN' ? '复制' : 'Copy') }}
                          </button>
                        </div>
                        <pre v-if="typeof step.arguments?.query === 'string'" class="code-pre sql-pre">{{ step.arguments.query }}</pre>
                        <pre v-else-if="step.arguments && (typeof step.arguments === 'string' || Object.keys(step.arguments).length > 0)" class="code-pre">{{ typeof step.arguments === 'string' ? step.arguments : JSON.stringify(step.arguments, null, 2) }}</pre>
                        <div v-else class="empty-param-hint">
                          {{ locale === 'zh-CN' ? '无输入参数（无参调用）' : 'No input arguments' }}
                        </div>
                      </div>

                      <!-- Tool Results: Table or JSON -->
                      <div class="result-block">
                        <div class="code-header-bar">
                          <span class="code-label-title">
                            <AppIcon name="overview" :size="12" />
                            {{ locale === 'zh-CN' ? '执行结果 (Output)' : 'Execution Output' }}
                            <span v-if="isTabular(step.result)" class="badge-count">
                              {{ step.result.length }} {{ locale === 'zh-CN' ? '条记录' : 'records' }}
                            </span>
                          </span>
                          <button
                            v-if="step.result"
                            type="button"
                            class="btn-copy-sm"
                            @click="copyText(typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2), 'res-' + idx)"
                          >
                            {{ copiedKey === 'res-' + idx ? (locale === 'zh-CN' ? '已复制 ✓' : 'Copied ✓') : (locale === 'zh-CN' ? '复制' : 'Copy') }}
                          </button>
                        </div>

                        <!-- Render Table if Tabular -->
                        <div class="apple-table-wrap" v-if="isTabular(step.result)">
                          <table class="apple-table">
                            <thead>
                              <tr>
                                <th v-for="h in getTableHeaders(step.result)" :key="h">{{ h }}</th>
                              </tr>
                            </thead>
                            <tbody>
                              <tr v-for="(row, rIdx) in (isTableExpanded(idx) ? step.result : step.result.slice(0, 10))" :key="rIdx">
                                <td v-for="h in getTableHeaders(step.result)" :key="h">{{ row[h] }}</td>
                              </tr>
                            </tbody>
                          </table>
                          <div class="table-more" v-if="step.result.length > 10">
                            <button type="button" class="btn-more-rows" @click="toggleTableExpand(idx)">
                              {{ isTableExpanded(idx) ? (locale === 'zh-CN' ? '收起多余记录' : 'Show less') : (locale === 'zh-CN' ? `展开全部 ${step.result.length} 条记录` : `Show all ${step.result.length} records`) }}
                            </button>
                          </div>
                        </div>

                        <!-- Render Formatted Code -->
                        <pre v-else-if="step.result" class="json-pre code-pre">{{ typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2) }}</pre>

                        <div v-else class="empty-param-hint">
                          {{ locale === 'zh-CN' ? '执行完成，无返回数据 (Empty output)' : 'Execution succeeded with empty output' }}
                        </div>
                      </div>

                      <!-- Evidence tags -->
                      <div class="evidence-block" v-if="step.evidence_ids && step.evidence_ids.length > 0">
                        <span class="evidence-label">{{ locale === 'zh-CN' ? '关联证据 (Evidence):' : 'Evidence:' }}</span>
                        <span v-for="ev in step.evidence_ids" :key="ev" class="evidence-tag">{{ ev }}</span>
                      </div>
                    </div>
                  </div>

                  <!-- Direct Reasoning / Fallback Node (When 0 tools called) -->
                  <div class="inner-step-card card card-direct" v-if="toolCallSteps.length === 0">
                    <div class="card-header">
                      <div class="tool-meta">
                        <span :class="['service-badge', selectedDetail.status === 'failed' ? 'badge-signoz' : 'badge-direct']">
                          {{ selectedDetail.status === 'failed' ? (locale === 'zh-CN' ? '未记录工具流水' : 'No Trace Recorded') : (locale === 'zh-CN' ? '端到端直接推理' : 'Direct Reasoning') }}
                        </span>
                        <strong class="tool-name">
                          {{ selectedDetail.status === 'failed'
                            ? (locale === 'zh-CN' ? '任务异常终止（未持久化调用明细）' : 'Run Interrupted before Trace Persisted')
                            : (selectedDetail.status === 'cancelled'
                              ? (locale === 'zh-CN' ? '任务已打断（未持久化完整流水）' : 'Run Cancelled before Finalization')
                              : (locale === 'zh-CN' ? '基于知识库与规则解答' : 'Knowledge-Grounded Resolution'))
                          }}
                        </strong>
                      </div>
                      <span class="direct-tag">
                        {{ selectedDetail.status === 'failed'
                          ? (locale === 'zh-CN' ? '异常退出' : 'Interrupted')
                          : (locale === 'zh-CN' ? '未触发外部 MCP 工具' : 'No External MCP')
                        }}
                      </span>
                    </div>
                    <div class="direct-desc">
                      {{ selectedDetail.status === 'failed'
                        ? (locale === 'zh-CN'
                          ? '该工单任务在终态持久化前发生异常中断或打断，历史推理中途执行的工具调用未完整落盘至流水记录表。'
                          : 'The job failed or was interrupted before trace telemetry could be fully committed.')
                        : (selectedDetail.status === 'cancelled'
                          ? (locale === 'zh-CN'
                            ? '该工单在推理途中收到新事件或被人工优先保护接管打断，未持久化完整工具调用记录。'
                            : 'The job was cancelled or intercepted before completing tool telemetry.')
                          : (locale === 'zh-CN'
                            ? '本工单诉求明确，AI 经深度思考后已直接在内置客服知识库与业务规则中命中最佳解决方案，未记录额外的外部工具调用。'
                            : 'The inquiry was resolved directly via internal guidelines without requiring additional external tool invocations.'))
                      }}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Step 4: Final English Customer Reply -->
            <div class="stream-node" v-if="selectedDetail.final_reply">
              <div class="node-marker marker-reply">📝</div>
              <div class="node-body card card-reply">
                <div class="card-header">
                  <span class="step-title">{{ t('runs.step.reply') }}</span>
                  <span class="lang-tag">English Only</span>
                </div>
                <div class="reply-content">
                  <pre class="reply-pre">{{ selectedDetail.final_reply }}</pre>
                </div>
              </div>
            </div>

            <!-- Step 5: Browser Relay Delivery Breakdown -->
            <div class="stream-node" v-if="selectedDetail.delivery">
              <div class="node-marker marker-delivery">🚀</div>
              <div class="node-body card card-delivery">
                <div class="card-header">
                  <span class="step-title">{{ t('runs.step.delivery') }}</span>
                  <span class="delivery-status sent">200 OK ({{ selectedDetail.delivery.status }})</span>
                </div>
                <div class="delivery-flow">
                  <div class="flow-step">
                    <small>通道</small>
                    <strong>{{ selectedDetail.delivery.channel }}</strong>
                  </div>
                  <div class="flow-arrow">→</div>
                  <div class="flow-step">
                    <small>总投递耗时</small>
                    <strong>{{ selectedDetail.delivery.total_duration_ms || '-' }}ms</strong>
                  </div>
                  <div class="flow-arrow" v-if="selectedDetail.delivery.timings?.channel_ms">→</div>
                  <div class="flow-step" v-if="selectedDetail.delivery.timings?.channel_ms">
                    <small>渠道切换</small>
                    <strong>{{ selectedDetail.delivery.timings.channel_ms }}ms</strong>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="empty-player" v-else-if="!loadingDetail">
          <AppIcon name="runs" :size="48" />
          <h3>{{ t('runs.empty.player.title') }}</h3>
          <p>{{ t('runs.empty.player.desc') }}</p>
        </div>

        <div class="loading-state" v-else>
          <p>{{ t('runs.loading.detail') }}</p>
        </div>
      </main>
    </div>

    <!-- Tab 2: Container Live Logs Console -->
    <div v-show="activeTab === 'console'" class="console-workspace">
      <div class="console-toolbar">
        <div class="toolbar-left">
          <span class="docker-badge">DOCKER LIVE</span>
          <div class="level-filters">
            <button :class="['level-pill', { active: logLevel === 'all' }]" @click="logLevel = 'all'">{{ t('runs.console.level.all') }}</button>
            <button :class="['level-pill warn', { active: logLevel === 'warn' }]" @click="logLevel = 'warn'">{{ t('runs.console.level.warn') }}</button>
            <button :class="['level-pill error', { active: logLevel === 'error' }]" @click="logLevel = 'error'">{{ t('runs.console.level.error') }}</button>
          </div>
          <div class="console-search">
            <AppIcon name="search" :size="13" class="search-icon" />
            <input v-model="logFilter" type="text" :placeholder="t('runs.console.search')" />
          </div>
        </div>
        <div class="toolbar-right">
          <label class="auto-refresh-label">
            <input type="checkbox" v-model="autoRefreshLogs" />
            <span>{{ t('runs.console.auto_refresh') }}</span>
          </label>
          <button class="btn-tool-refresh" :disabled="loadingLogs" @click="loadLiveLogs">
            <AppIcon name="refresh" :size="13" :class="{ spinning: loadingLogs }" />
            <span>{{ loadingLogs ? t('runs.console.reading') : t('runs.console.refresh_btn') }}</span>
          </button>
          <span class="log-count">{{ filteredLogs.length }} / {{ liveLogs.length }} 行</span>
        </div>
      </div>

      <div class="terminal-view">
        <div v-if="filteredLogs.length === 0" class="terminal-empty">
          <p>{{ t('runs.console.empty') }}</p>
        </div>
        <div
          v-for="(line, idx) in filteredLogs"
          :key="idx"
          :class="['log-row', getLineClass(line)]"
        >
          <span class="row-num">{{ idx + 1 }}</span>
          <span class="row-text">{{ line }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trace-workspace {
  display: flex;
  height: 100%;
  overflow: hidden;
  background: var(--surface);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
}

/* Sidebar */
.trace-sidebar {
  width: 320px;
  flex: 0 0 320px;
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  background: var(--canvas);
}

.trace-sidebar__header {
  padding: 16px;
  border-bottom: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--canvas);
}

.header-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-title-row h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
}

.btn-icon {
  background: transparent;
  border: none;
  color: var(--muted);
  padding: 4px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-icon:hover {
  color: var(--ink);
  background: var(--surface-hover);
}

.search-box {
  position: relative;
  display: flex;
  align-items: center;
}
.search-icon {
  position: absolute;
  left: 10px;
  color: var(--muted);
}
.search-box input {
  width: 100%;
  height: 34px;
  padding-left: 32px;
  padding-right: 10px;
  border-radius: 8px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
  font-size: 12px;
  outline: none;
  transition: border-color 0.15s;
}
.search-box input:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-soft);
}

.filter-pills {
  display: flex;
  gap: 6px;
}
.pill {
  flex: 1;
  height: 26px;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  font-size: 11px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.pill:hover {
  background: var(--surface-hover);
  color: var(--ink);
}
.pill.active {
  background: var(--ink);
  color: var(--surface);
  border-color: var(--ink);
}

/* Ticket List */
.ticket-list {
  flex: 1;
  overflow-y: auto;
}

.ticket-item {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  background: var(--surface);
  transition: background 0.12s;
}
.ticket-item:hover {
  background: var(--surface-hover);
}
.ticket-item.active {
  background: var(--surface-soft);
  border-left: 3px solid var(--blue);
  box-shadow: inset 0 0 0 1px var(--blue-soft);
}

.ticket-item__top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.ticket-id {
  font-weight: 700;
  font-size: 13px;
  color: #0071e3;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #86868b;
}
.status-dot.completed { background: #34c759; }
.status-dot.running { background: #0071e3; }
.status-dot.failed { background: #ff3b30; }

.status-tag {
  font-size: 10px;
  color: #86868b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.duration-badge {
  margin-left: auto;
  font-size: 11px;
  color: #86868b;
  font-family: ui-monospace, monospace;
}

.ticket-email {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 3px;
}

.ticket-preview {
  font-size: 12px;
  color: var(--ink-soft);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 8px;
}

.ticket-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 10px;
  color: var(--muted);
}
.meta-tag {
  background: var(--surface-soft);
  border: 1px solid var(--line);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--ink-soft);
}
.meta-time {
  margin-left: auto;
  font-family: ui-monospace, monospace;
}

/* Player Column */
.trace-player {
  flex: 1;
  overflow-y: auto;
  padding: 24px 36px;
  background: var(--surface);
}

.action-banner {
  padding: 10px 16px;
  border-radius: 8px;
  background: var(--blue-soft);
  color: var(--blue);
  border: 1px solid rgba(0, 113, 227, 0.2);
  font-size: 13px;
  margin-bottom: 16px;
}

.player-hero {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}

.hero-title-line {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hero-title-line h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.02em;
}

.status-pill {
  padding: 3px 8px;
  border-radius: 12px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}
.status-pill.completed { background: var(--green-soft); color: var(--green); }
.status-pill.running { background: var(--blue-soft); color: var(--blue); }
.status-pill.failed { background: var(--red-soft); color: var(--red); }

.hero-sub {
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
  display: flex;
  gap: 8px;
}
.hero-sub .divider { color: var(--line-strong); }

.hero-actions {
  display: flex;
  gap: 10px;
}
.btn-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 14px;
  border-radius: 8px;
  border: none;
  background: var(--ink);
  color: var(--surface);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  text-decoration: none;
}
.btn-action:hover {
  opacity: 0.9;
}
.btn-secondary {
  background: var(--surface);
  color: var(--ink);
  border: 1px solid var(--line-strong);
}
.btn-secondary:hover {
  background: var(--surface-hover);
}
.btn-skip {
  background: var(--surface-soft);
  color: var(--muted);
  border: 1px solid var(--line);
}
.btn-skip:hover {
  background: var(--surface-hover);
  color: var(--ink);
}
.btn-escalate {
  background: var(--red-soft);
  color: var(--red);
  border: 1px solid rgba(255, 59, 48, 0.3);
}
.btn-escalate:hover {
  background: rgba(255, 59, 48, 0.2);
}

/* Timeline Stream */
.timeline-stream {
  display: flex;
  flex-direction: column;
  gap: 20px;
  position: relative;
}
.timeline-stream::before {
  content: "";
  position: absolute;
  top: 15px;
  bottom: 15px;
  left: 17px;
  width: 2px;
  background: var(--line);
}

.stream-node {
  display: flex;
  gap: 16px;
  position: relative;
}
.node-marker {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--surface);
  border: 1px solid var(--line);
  display: grid;
  place-items: center;
  font-size: 15px;
  z-index: 2;
  flex: 0 0 36px;
}

.card {
  flex: 1;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.step-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.timestamp {
  font-size: 11px;
  color: var(--muted);
  font-family: ui-monospace, monospace;
}

.customer-message-box {
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}

/* Collapsible Processing Log Node */
.marker-processing {
  background: rgba(0, 113, 227, 0.1);
  border-color: rgba(0, 113, 227, 0.25);
  color: var(--accent);
}

.card-processing-log {
  background: var(--surface);
  border: 1px solid rgba(0, 113, 227, 0.22);
  transition: all 0.2s ease;
}

.card-processing-log:hover {
  border-color: rgba(0, 113, 227, 0.45);
}

.processing-header {
  margin-bottom: 0;
  user-select: none;
}

.processing-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.processing-icon {
  display: flex;
  align-items: center;
  color: var(--accent);
}

.processing-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.badge-tool-count {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(0, 113, 227, 0.1);
  color: #0071e3;
}

.badge-tool-count.badge-direct {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
}

.badge-thinking-tag {
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(175, 82, 222, 0.12);
  color: #8b5cf6;
}

.badge-duration {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-soft);
  color: var(--muted);
  font-family: ui-monospace, monospace;
}

.btn-toggle-processing-log {
  background: rgba(0, 113, 227, 0.08);
  color: var(--accent);
  border: 1px solid rgba(0, 113, 227, 0.2);
  font-size: 12px;
  font-weight: 600;
  padding: 5px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-toggle-processing-log:hover {
  background: rgba(0, 113, 227, 0.16);
  border-color: rgba(0, 113, 227, 0.4);
}

.processing-log-body {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--line);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.inner-step-card {
  margin-bottom: 0;
}

/* Thinking Stream */
.card-thinking {
  background: var(--surface);
  border-color: rgba(175, 82, 222, 0.35);
}
.thinking-toggle {
  cursor: pointer;
  margin-bottom: 0;
}
.thinking-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #8b5cf6;
  font-size: 13px;
}
.btn-text {
  background: none;
  border: none;
  color: #86868b;
  font-size: 11px;
  cursor: pointer;
}
.thinking-content {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(175, 82, 222, 0.15);
}
.thinking-content pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink);
}

/* Tool Stream */
.card-tool {
  background: var(--surface);
  border: 1px solid var(--line);
  transition: box-shadow 0.15s ease;
}
.card-tool:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}
.cursor-pointer {
  cursor: pointer;
  user-select: none;
}
.tool-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.service-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  text-transform: uppercase;
}
.badge-db { background: rgba(0, 113, 227, 0.1); color: #0071e3; }
.badge-signoz { background: rgba(245, 166, 35, 0.12); color: #d97706; }
.badge-docs { background: rgba(20, 184, 166, 0.12); color: #0d9488; }
.badge-zd { background: rgba(52, 199, 89, 0.12); color: #059669; }
.badge-source { background: rgba(59, 130, 246, 0.12); color: #2563eb; }
.badge-github { background: rgba(100, 116, 139, 0.12); color: #475569; }
.badge-feishu { background: rgba(0, 180, 42, 0.12); color: #009a29; }
.badge-relay { background: rgba(175, 82, 222, 0.12); color: #8b5cf6; }
.badge-direct { background: rgba(16, 185, 129, 0.12); color: #059669; }
.badge-default { background: var(--surface-soft); color: var(--muted); }

.tool-name {
  font-size: 13px;
  font-family: "SF Mono", Menlo, monospace;
  color: var(--ink);
  font-weight: 600;
}
.status-pill {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.pill-ok {
  background: rgba(52, 199, 89, 0.12);
  color: #059669;
}
.pill-fail {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
}
.call-id-tag {
  font-size: 10px;
  color: var(--muted);
  font-family: "SF Mono", Menlo, monospace;
  background: var(--surface-soft);
  padding: 1px 5px;
  border-radius: 4px;
}
.step-index {
  font-size: 11px;
  color: var(--muted);
}
.tool-toggle-btn {
  color: var(--accent);
  font-weight: 500;
  padding: 2px 6px;
  border-radius: 4px;
}
.tool-toggle-btn:hover {
  background: var(--surface-soft);
}

.tool-expanded-body {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

.code-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 10px 0 6px;
}
.code-label-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink);
  display: flex;
  align-items: center;
  gap: 6px;
}
.badge-count {
  font-size: 10px;
  font-weight: 500;
  background: var(--surface-soft);
  color: var(--muted);
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px solid var(--line);
}
.btn-copy-sm {
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-copy-sm:hover {
  color: var(--ink);
  border-color: var(--ink);
  background: var(--surface-soft);
}

.code-block pre, .json-pre, .code-pre {
  margin: 0;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink);
  overflow-x: auto;
  max-height: 380px;
}
.sql-pre {
  border-left: 3px solid #0071e3;
  background: var(--surface-soft);
  font-size: 12px;
  line-height: 1.6;
}

.empty-param-hint {
  font-size: 12px;
  color: var(--muted);
  font-style: italic;
  padding: 8px 12px;
  background: var(--surface-soft);
  border-radius: 6px;
  border: 1px dashed var(--line);
}

/* Apple Table */
.apple-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  max-height: 360px;
}
.apple-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  text-align: left;
}
.apple-table th {
  padding: 8px 12px;
  background: var(--surface-soft);
  color: var(--muted);
  font-weight: 600;
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 1;
}
.apple-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  color: var(--ink);
  font-family: "SF Mono", Menlo, monospace;
  white-space: nowrap;
}
.apple-table tr:hover td {
  background: rgba(0, 113, 227, 0.04);
}
.table-more {
  padding: 8px 12px;
  background: var(--surface-soft);
  border-top: 1px solid var(--line);
  text-align: center;
}
.btn-more-rows {
  font-size: 11px;
  font-weight: 500;
  color: var(--accent);
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 4px;
}
.btn-more-rows:hover {
  text-decoration: underline;
}

/* Evidence Tags */
.evidence-block {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.evidence-label {
  font-size: 11px;
  color: var(--muted);
  font-weight: 600;
}
.evidence-tag {
  font-size: 10px;
  font-family: "SF Mono", Menlo, monospace;
  background: var(--surface-soft);
  color: var(--muted);
  border: 1px solid var(--line);
  padding: 1px 6px;
  border-radius: 4px;
}

/* Direct Reasoning Card */
.marker-direct {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
}
.card-direct {
  background: var(--surface);
  border: 1px dashed rgba(16, 185, 129, 0.35);
  border-radius: 12px;
  padding: 14px 16px;
}
.direct-tag {
  font-size: 11px;
  color: var(--muted);
  background: var(--surface-soft);
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--line);
}
.direct-desc {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
  margin-top: 8px;
}

/* Notice & Protection */
.card-notice {
  background: var(--surface-soft);
  border-color: var(--line);
}
.card-notice.cancelled {
  background: var(--blue-soft);
  border-color: rgba(0, 113, 227, 0.25);
}
.card-notice.failed {
  background: var(--red-soft);
  border-color: rgba(255, 59, 48, 0.25);
}
.notice-box {
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
  background: var(--surface);
  border-radius: 8px;
  border: 1px solid var(--line);
}

/* Reply */
.card-reply {
  border-color: rgba(52, 199, 89, 0.3);
}
.lang-tag {
  background: var(--green-soft);
  color: var(--green);
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
}
.reply-pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
  background: var(--surface-soft);
  border: 1px solid var(--line);
  padding: 14px 16px;
  border-radius: 8px;
}

/* Delivery */
.card-delivery {
  border-color: rgba(175, 82, 222, 0.3);
}
.delivery-status.sent {
  color: #8b5cf6;
  font-weight: 600;
  font-size: 11px;
}
.delivery-flow {
  display: flex;
  align-items: center;
  gap: 16px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  padding: 12px 16px;
  border-radius: 8px;
}
.flow-step small {
  display: block;
  font-size: 10px;
  color: var(--muted);
  margin-bottom: 2px;
}
.flow-step strong {
  font-size: 13px;
  color: var(--ink);
}
.flow-arrow {
  color: var(--muted);
}

/* Empty & Loading */
.empty-player, .empty-list, .loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 60%;
  color: var(--muted);
  text-align: center;
}
.empty-player h3 {
  margin: 12px 0 4px;
  color: var(--ink);
}

/* Page Container & Top Bar */
.runs-page-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--surface);
}

.runs-nav-bar {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}

.nav-tabs {
  display: flex;
  background: var(--surface-soft);
  padding: 3px;
  border-radius: 8px;
  gap: 2px;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 14px;
  border: none;
  background: transparent;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
}

.tab-btn:hover {
  color: var(--ink);
}

.tab-btn.active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--shadow-sm);
}

.tab-badge {
  background: var(--surface-hover);
  color: var(--ink);
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 10px;
}

.pulse-indicator {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #34c759;
  box-shadow: 0 0 6px #34c759;
  animation: pulse-glow 2s infinite ease-in-out;
}

@keyframes pulse-glow {
  0%, 100% { opacity: 0.5; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.2); }
}

.trace-workspace {
  flex: 1;
  min-height: 0;
}

/* Container Live Console Workspace */
.console-workspace {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  background: #0d1117;
  color: #c9d1d9;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

.console-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 18px;
  background: #161b22;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
}

.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.docker-badge {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  background: rgba(56, 139, 253, 0.15);
  color: #58a6ff;
  border: 1px solid rgba(56, 139, 253, 0.3);
  padding: 3px 7px;
  border-radius: 4px;
}

.level-filters {
  display: flex;
  gap: 4px;
  background: #21262d;
  padding: 2px;
  border-radius: 6px;
}

.level-pill {
  border: none;
  background: transparent;
  color: #8b949e;
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s;
}

.level-pill:hover {
  color: #c9d1d9;
}

.level-pill.active {
  background: #30363d;
  color: #f0f6fc;
}

.level-pill.warn.active {
  background: rgba(210, 153, 34, 0.25);
  color: #e3b341;
}

.level-pill.error.active {
  background: rgba(248, 81, 73, 0.25);
  color: #f85149;
}

.console-search {
  position: relative;
  display: flex;
  align-items: center;
}

.console-search .search-icon {
  position: absolute;
  left: 9px;
  color: #8b949e;
}

.console-search input {
  background: #21262d;
  border: 1px solid #30363d;
  border-radius: 6px;
  color: #c9d1d9;
  padding: 5px 10px 5px 28px;
  font-size: 12px;
  width: 240px;
  outline: none;
}

.console-search input:focus {
  border-color: #58a6ff;
}

.auto-refresh-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #8b949e;
  cursor: pointer;
}

.btn-tool-refresh {
  display: flex;
  align-items: center;
  gap: 5px;
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  font-size: 12px;
  padding: 5px 11px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.12s;
}

.btn-tool-refresh:hover:not(:disabled) {
  background: #30363d;
  color: #f0f6fc;
}

.btn-tool-refresh:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.log-count {
  font-size: 11px;
  color: #8b949e;
}

.terminal-view {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px;
  font-size: 12px;
  line-height: 1.6;
}

.terminal-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 80%;
  color: #8b949e;
}

.log-row {
  display: flex;
  gap: 12px;
  padding: 1px 4px;
  border-radius: 3px;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-row:hover {
  background: rgba(255, 255, 255, 0.04);
}

.row-num {
  color: #484f58;
  user-select: none;
  min-width: 38px;
  text-align: right;
  flex-shrink: 0;
}

.row-text {
  flex: 1;
}

.log-row.line-error {
  background: rgba(248, 81, 73, 0.12);
  color: #ff7b72;
}

.log-row.line-warn {
  background: rgba(210, 153, 34, 0.1);
  color: #d29922;
}

.log-row.line-http {
  color: #79c0ff;
}

.log-row.line-worker {
  color: #7ee787;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
