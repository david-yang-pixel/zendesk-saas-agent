<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

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
    toolName?: string
    service?: string
    arguments?: any
    result?: any
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

function serviceBadgeClass(service?: string): string {
  if (service === 'db-gateway') return 'badge-db'
  if (service === 'signoz') return 'badge-signoz'
  if (service === 'docs') return 'badge-docs'
  if (service === 'zendesk') return 'badge-zd'
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
  <div class="trace-workspace">
    <!-- Left Column: Tickets Stream -->
    <aside class="trace-sidebar">
      <div class="trace-sidebar__header">
        <div class="header-title-row">
          <h3>工单推演流水</h3>
          <button class="btn-icon" title="刷新" @click="loadTraces" :disabled="loadingList">
            <AppIcon name="refresh" :size="15" />
          </button>
        </div>
        <div class="search-box">
          <AppIcon name="search" :size="14" class="search-icon" />
          <input
            v-model="query"
            type="text"
            placeholder="搜索工单号 / 邮箱 / 消息..."
            @input="loadTraces"
          />
        </div>
        <div class="filter-pills">
          <button
            :class="['pill', { active: statusFilter === 'all' }]"
            @click="statusFilter = 'all'; loadTraces()"
          >全部</button>
          <button
            :class="['pill', { active: statusFilter === 'completed' }]"
            @click="statusFilter = 'completed'; loadTraces()"
          >已完成</button>
          <button
            :class="['pill', { active: statusFilter === 'running' }]"
            @click="statusFilter = 'running'; loadTraces()"
          >运行中</button>
          <button
            :class="['pill', { active: statusFilter === 'failed' }]"
            @click="statusFilter = 'failed'; loadTraces()"
          >失败</button>
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
          <div class="ticket-preview">{{ item.customer_message || '无消息文本' }}</div>
          <div class="ticket-meta">
            <span class="meta-tag" v-if="item.tool_calls_count > 0">🛠️ {{ item.tool_calls_count }} 工具</span>
            <span class="meta-tag" v-if="item.delivery_status">🚀 {{ item.delivery_status }}</span>
            <span class="meta-time">{{ item.created_at.slice(11, 19) }}</span>
          </div>
        </div>
      </div>

      <div class="empty-list" v-else-if="!loadingList">
        <AppIcon name="runs" :size="24" />
        <p>暂无符合条件的工单</p>
      </div>

      <div class="loading-state" v-else>
        <p>正在载入流水...</p>
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
              <h2>工单 #{{ selectedDetail.ticket_id }} 推演审计</h2>
              <span :class="['status-pill', selectedDetail.status]">{{ selectedDetail.status }}</span>
            </div>
            <div class="hero-sub">
              <span>客户: <strong>{{ selectedDetail.requester_email || '未知客户' }}</strong></span>
              <span class="divider">·</span>
              <span>模型: <strong>{{ selectedDetail.model || 'gpt-5.6-terra' }}</strong></span>
              <span class="divider">·</span>
              <span>总耗时: <strong>{{ selectedDetail.duration }}</strong></span>
            </div>
          </div>
          <div class="hero-actions">
            <button class="btn-action" @click="retryCurrentJob">
              <AppIcon name="refresh" :size="14" />
              <span>一键重试工单</span>
            </button>
            <a
              :href="'https://langgenius.zendesk.com/agent/tickets/' + selectedDetail.ticket_id"
              target="_blank"
              class="btn-action btn-secondary"
            >
              <span>查看 Zendesk 原单</span>
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
                <span class="step-title">客户原始提问 (Customer Message)</span>
                <span class="timestamp">{{ selectedDetail.created_at }}</span>
              </div>
              <div class="customer-message-box">
                {{ selectedDetail.customer_message || '未获取到客户原始消息' }}
              </div>
            </div>
          </div>

          <!-- Step 2: Reasoning / Thinking -->
          <div class="stream-node" v-for="(step, idx) in selectedDetail.steps.filter(s => s.type === 'thinking')" :key="'th-' + idx">
            <div class="node-marker marker-thinking">🧠</div>
            <div class="node-body card card-thinking">
              <div class="card-header thinking-toggle" @click="thinkingExpanded = !thinkingExpanded">
                <div class="thinking-label">
                  <AppIcon name="spark" :size="14" />
                  <strong>深度思考链 (Reasoning Process)</strong>
                </div>
                <button class="btn-text">{{ thinkingExpanded ? '折叠' : '展开' }}</button>
              </div>
              <div class="thinking-content" v-if="thinkingExpanded">
                <pre>{{ step.thinking }}</pre>
              </div>
            </div>
          </div>

          <!-- Step 3: Tool Calls -->
          <div class="stream-node" v-for="(step, idx) in selectedDetail.steps.filter(s => s.type === 'tool_call')" :key="'tc-' + idx">
            <div class="node-marker marker-tool">🛠️</div>
            <div class="node-body card card-tool">
              <div class="card-header">
                <div class="tool-meta">
                  <span :class="['service-badge', serviceBadgeClass(step.service)]">{{ step.service || 'MCP' }}</span>
                  <strong class="tool-name">{{ step.toolName }}</strong>
                </div>
                <span class="step-index">Tool Call #{{ idx + 1 }}</span>
              </div>

              <!-- Tool Arguments -->
              <div class="code-block input-block" v-if="step.arguments">
                <div class="code-label">输入参数 (Arguments):</div>
                <pre v-if="typeof step.arguments?.query === 'string'">{{ step.arguments.query }}</pre>
                <pre v-else>{{ JSON.stringify(step.arguments, null, 2) }}</pre>
              </div>

              <!-- Tool Results: Table or JSON -->
              <div class="result-block" v-if="step.result">
                <div class="code-label">执行结果 (Output):</div>
                <!-- Render Table if Tabular -->
                <div class="apple-table-wrap" v-if="isTabular(step.result)">
                  <table class="apple-table">
                    <thead>
                      <tr>
                        <th v-for="h in getTableHeaders(step.result)" :key="h">{{ h }}</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, rIdx) in step.result.slice(0, 10)" :key="rIdx">
                        <td v-for="h in getTableHeaders(step.result)" :key="h">{{ row[h] }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <div class="table-more" v-if="step.result.length > 10">
                    ... 还有 {{ step.result.length - 10 }} 条数据
                  </div>
                </div>
                <!-- Render Formatted Code -->
                <pre v-else class="json-pre">{{ typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2) }}</pre>
              </div>
            </div>
          </div>

          <!-- Step 4: Final English Customer Reply -->
          <div class="stream-node" v-if="selectedDetail.final_reply">
            <div class="node-marker marker-reply">📝</div>
            <div class="node-body card card-reply">
              <div class="card-header">
                <span class="step-title">最终生成的客服答复 (Final English Reply)</span>
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
                <span class="step-title">Zendesk Browser Relay 前台投递回执</span>
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
        <h3>选择左侧工单以回放推演</h3>
        <p>查看完整的思维链、SQL执行数据与前台投递流水</p>
      </div>

      <div class="loading-state" v-else>
        <p>正在加载推演详情...</p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.trace-workspace {
  display: flex;
  height: calc(100vh - 68px);
  overflow: hidden;
  background: #0d0d0e;
  color: #f5f5f7;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
}

/* Sidebar */
.trace-sidebar {
  width: 380px;
  flex: 0 0 380px;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  background: #111113;
}

.trace-sidebar__header {
  padding: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  gap: 12px;
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
  letter-spacing: -0.01em;
}

.btn-icon {
  background: transparent;
  border: none;
  color: #a1a1a6;
  padding: 4px;
  border-radius: 6px;
}
.btn-icon:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}

.search-box {
  position: relative;
  display: flex;
  align-items: center;
}
.search-icon {
  position: absolute;
  left: 10px;
  color: #71717a;
}
.search-box input {
  width: 100%;
  height: 34px;
  padding-left: 32px;
  padding-right: 10px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: #18181b;
  color: #fff;
  font-size: 12px;
  outline: none;
}
.search-box input:focus {
  border-color: #3b82f6;
}

.filter-pills {
  display: flex;
  gap: 6px;
}
.pill {
  flex: 1;
  height: 26px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: transparent;
  color: #a1a1a6;
  font-size: 11px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.pill.active {
  background: #27272a;
  color: #fff;
  border-color: rgba(255, 255, 255, 0.2);
}

/* Ticket List */
.ticket-list {
  flex: 1;
  overflow-y: auto;
}

.ticket-item {
  padding: 14px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  cursor: pointer;
  transition: background 0.15s;
}
.ticket-item:hover {
  background: #18181b;
}
.ticket-item.active {
  background: #222226;
  border-left: 3px solid #3b82f6;
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
  color: #fff;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #a1a1a6;
}
.status-dot.completed { background: #10b981; box-shadow: 0 0 6px rgba(16,185,129,0.5); }
.status-dot.running { background: #3b82f6; box-shadow: 0 0 6px rgba(59,130,246,0.5); }
.status-dot.failed { background: #ef4444; box-shadow: 0 0 6px rgba(239,68,68,0.5); }

.status-tag {
  font-size: 10px;
  color: #a1a1a6;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.duration-badge {
  margin-left: auto;
  font-size: 11px;
  color: #71717a;
  font-family: ui-monospace, monospace;
}

.ticket-email {
  font-size: 11px;
  color: #a1a1a6;
  margin-bottom: 3px;
}

.ticket-preview {
  font-size: 12px;
  color: #d4d4d8;
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
  color: #71717a;
}
.meta-tag {
  background: #27272a;
  padding: 2px 6px;
  border-radius: 4px;
}
.meta-time {
  margin-left: auto;
  font-family: ui-monospace, monospace;
}

/* Player Column */
.trace-player {
  flex: 1;
  overflow-y: auto;
  padding: 28px 40px;
}

.action-banner {
  padding: 10px 16px;
  border-radius: 8px;
  background: #1e3a8a;
  color: #bfdbfe;
  font-size: 13px;
  margin-bottom: 16px;
}

.player-hero {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  margin-bottom: 28px;
}

.hero-title-line {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hero-title-line h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.status-pill {
  padding: 3px 8px;
  border-radius: 12px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}
.status-pill.completed { background: rgba(16,185,129,0.15); color: #34d399; }
.status-pill.running { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-pill.failed { background: rgba(239,68,68,0.15); color: #f87171; }

.hero-sub {
  margin-top: 6px;
  color: #a1a1a6;
  font-size: 12px;
  display: flex;
  gap: 8px;
}
.hero-sub .divider { color: #52525b; }

.hero-actions {
  display: flex;
  gap: 10px;
}
.btn-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: #fff;
  color: #000;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  text-decoration: none;
}
.btn-action:hover {
  background: #f4f4f5;
}
.btn-secondary {
  background: #27272a;
  color: #e4e4e7;
  border-color: rgba(255, 255, 255, 0.1);
}
.btn-secondary:hover {
  background: #3f3f46;
}

/* Timeline Stream */
.timeline-stream {
  display: flex;
  flex-direction: column;
  gap: 24px;
  position: relative;
}
.timeline-stream::before {
  content: "";
  position: absolute;
  top: 15px;
  bottom: 15px;
  left: 17px;
  width: 2px;
  background: rgba(255, 255, 255, 0.08);
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
  background: #18181b;
  border: 1px solid rgba(255, 255, 255, 0.15);
  display: grid;
  place-items: center;
  font-size: 15px;
  z-index: 2;
  flex: 0 0 36px;
}

.card {
  flex: 1;
  background: #141416;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.step-title {
  font-size: 13px;
  font-weight: 600;
  color: #d4d4d8;
}
.timestamp {
  font-size: 11px;
  color: #71717a;
  font-family: ui-monospace, monospace;
}

.customer-message-box {
  background: #1c1c1f;
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.6;
  color: #fafafa;
}

/* Thinking Stream */
.card-thinking {
  background: #121214;
  border-color: rgba(147, 51, 234, 0.2);
}
.thinking-toggle {
  cursor: pointer;
  margin-bottom: 0;
}
.thinking-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #c084fc;
  font-size: 13px;
}
.btn-text {
  background: none;
  border: none;
  color: #a1a1a6;
  font-size: 11px;
  cursor: pointer;
}
.thinking-content {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.thinking-content pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: "JetBrains Mono", SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #d8b4fe;
}

/* Tool Stream */
.card-tool {
  background: #141416;
}
.tool-meta {
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
.badge-db { background: rgba(59, 130, 246, 0.2); color: #60a5fa; }
.badge-signoz { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
.badge-docs { background: rgba(20, 184, 166, 0.2); color: #2dd4bf; }
.badge-zd { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.badge-relay { background: rgba(168, 85, 247, 0.2); color: #c084fc; }
.badge-default { background: rgba(255, 255, 255, 0.1); color: #fff; }

.tool-name {
  font-size: 13px;
  font-family: ui-monospace, monospace;
  color: #f4f4f5;
}
.step-index {
  font-size: 11px;
  color: #71717a;
}

.code-label {
  font-size: 11px;
  color: #a1a1a6;
  margin: 10px 0 6px;
}
.code-block pre, .json-pre {
  margin: 0;
  background: #09090b;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 10px 12px;
  font-family: "JetBrains Mono", SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #e4e4e7;
  overflow-x: auto;
}

/* Apple Table */
.apple-table-wrap {
  overflow-x: auto;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  background: #09090b;
}
.apple-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  text-align: left;
}
.apple-table th {
  padding: 8px 12px;
  background: #18181b;
  color: #a1a1a6;
  font-weight: 600;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.apple-table td {
  padding: 8px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  color: #f4f4f5;
  font-family: "JetBrains Mono", monospace;
}
.table-more {
  padding: 6px 12px;
  font-size: 11px;
  color: #71717a;
}

/* Reply */
.card-reply {
  border-color: rgba(16, 185, 129, 0.2);
}
.lang-tag {
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
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
  color: #f4f4f5;
  background: #18181b;
  padding: 14px 16px;
  border-radius: 8px;
}

/* Delivery */
.card-delivery {
  border-color: rgba(168, 85, 247, 0.2);
}
.delivery-status.sent {
  color: #c084fc;
  font-weight: 600;
  font-size: 11px;
}
.delivery-flow {
  display: flex;
  align-items: center;
  gap: 16px;
  background: #18181b;
  padding: 12px 16px;
  border-radius: 8px;
}
.flow-step small {
  display: block;
  font-size: 10px;
  color: #71717a;
  margin-bottom: 2px;
}
.flow-step strong {
  font-size: 13px;
  color: #fff;
}
.flow-arrow {
  color: #52525b;
}

/* Empty & Loading */
.empty-player, .empty-list, .loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 60%;
  color: #71717a;
  text-align: center;
}
.empty-player h3 {
  margin: 12px 0 4px;
  color: #d4d4d8;
}
</style>
