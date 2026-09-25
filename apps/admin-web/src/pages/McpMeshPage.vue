<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

interface McpService {
  id: string
  name: string
  label: string
  service_type: 'query' | 'action'
  endpoint: string
  online: boolean
  latency_ms: number
  tool_count: number
  tools: any[]
  error?: string
}

const services = ref<McpService[]>([])
const loading = ref(true)
const errorMessage = ref('')
const selectedService = ref<McpService | null>(null)
const selectedTool = ref<any | null>(null)

function applyMesh(nextServices: McpService[]) {
  const previousServiceId = selectedService.value?.id
  const previousToolName = selectedTool.value?.name
  services.value = nextServices
  selectedService.value = nextServices.find(service => service.id === previousServiceId) || nextServices[0] || null
  selectedTool.value = selectedService.value?.tools.find(tool => tool.name === previousToolName)
    || selectedService.value?.tools[0]
    || null
}

async function loadMesh() {
  loading.value = true
  errorMessage.value = ''
  probeResult.value = null
  try {
    const res = await api.getMcpMesh()
    applyMesh(res.services || [])
  } catch (err: any) {
    applyMesh([])
    errorMessage.value = err.message || '无法获取实时工具状态'
  } finally {
    loading.value = false
  }
}

const probing = ref(false)
const probeResult = ref<{
  ok: boolean
  latency_ms: number
  status: string
  detail: string
} | null>(null)

function selectService(s: McpService) {
  selectedService.value = s
  selectedTool.value = s.tools.length > 0 ? s.tools[0] : null
  probeResult.value = null
}

function selectTool(t: any) {
  selectedTool.value = t
  probeResult.value = null
}

async function runToolProbe() {
  if (!selectedService.value) return
  const serviceId = selectedService.value.id
  probing.value = true
  probeResult.value = null
  errorMessage.value = ''
  try {
    const res = await api.getMcpMesh()
    applyMesh(res.services || [])
    const current = services.value.find(service => service.id === serviceId)
    probeResult.value = {
      ok: current?.online === true,
      latency_ms: current?.latency_ms || 0,
      status: current?.online ? '连接可用' : '连接不可用',
      detail: current?.online
        ? `工具目录已返回 ${current.tool_count} 个工具；未执行选中工具。`
        : current?.error || '无法获取该服务的工具目录。',
    }
  } catch (err: any) {
    applyMesh([])
    errorMessage.value = err.message || '连接探测失败'
    probeResult.value = {
      ok: false,
      latency_ms: 0,
      status: '连接探测失败',
      detail: errorMessage.value,
    }
  } finally {
    probing.value = false
  }
}

onMounted(() => {
  void loadMesh()
})
</script>

<template>
  <div class="mcp-mesh-page">
    <div class="mesh-container">
      <header class="mesh-header">
        <div class="header-left">
          <h2>{{ t('tools.title') }}</h2>
        </div>
        <div class="header-actions">
          <button class="btn-refresh" :disabled="loading" @click="loadMesh">
            <AppIcon name="refresh" :size="15" :class="{ spinning: loading }" />
            <span>{{ loading ? t('tools.btn.probing') : t('tools.btn.refresh') }}</span>
          </button>
        </div>
      </header>

      <div v-if="errorMessage" class="mesh-notice" role="alert">{{ errorMessage }}</div>
      <div v-if="services.length === 0" class="mesh-empty">
        {{ loading ? t('tools.empty.loading') : t('tools.empty.unavailable') }}
      </div>

      <div class="services-grid">
        <div
          v-for="s in services"
          :key="s.id"
          :class="['service-card', { active: selectedService?.id === s.id, offline: !s.online }]"
          @click="selectService(s)"
        >
          <div class="card-top">
            <span :class="['type-pill', s.service_type]">{{ s.service_type === 'action' ? t('tools.type.action') : t('tools.type.query') }}</span>
            <div class="latency-badge">
              <span :class="['status-dot', { online: s.online, pulsing: loading }]"></span>
              <span>{{ s.online ? s.latency_ms + 'ms' : t('tools.status.offline') }}</span>
            </div>
          </div>
          <h4 class="service-name">{{ s.label }}</h4>
          <code class="service-id">{{ s.id }}</code>
          <div class="service-footer">
            <span class="tools-count">{{ s.tool_count }} {{ t('tools.count.tools') }}</span>
            <span class="arrow-icon">→</span>
          </div>
        </div>
      </div>

      <!-- Live tool directory and connection probe -->
      <div class="mesh-detail-split" v-if="selectedService">
        <!-- Left: Tool List of Selected Service -->
        <div class="tools-sidebar">
          <div class="tools-sidebar__header">
            <h4>{{ selectedService.label }} {{ t('tools.list.header') }}</h4>
            <span class="count-tag">{{ selectedService.tools.length }}</span>
          </div>
          <div class="tools-list">
            <div
              v-for="tool in selectedService.tools"
              :key="tool.name"
              :class="['tool-item', { active: selectedTool?.name === tool.name }]"
              @click="selectTool(tool)"
            >
              <div class="tool-item-name">{{ tool.name }}</div>
              <div class="tool-item-desc">{{ tool.description || t('tools.nodesc') }}</div>
            </div>
          </div>
        </div>

        <!-- Right: Tool Schema & Inspector -->
        <div class="tool-inspector">
          <div class="inspector-header">
            <div class="inspector-title">
              <h3>{{ selectedTool?.name || selectedService.label }}</h3>
              <span :class="['type-pill', selectedService.service_type]">
                {{ selectedService.id }}
              </span>
            </div>
            <div class="inspector-url">
              <small>{{ t('tools.endpoint') }}</small>
              <code>{{ selectedService.endpoint }}</code>
            </div>
          </div>

          <div v-if="selectedService.error" class="mesh-notice" role="alert">
            {{ selectedService.error }}
          </div>

          <div v-if="selectedTool" class="inspector-body">
            <div class="desc-card">
              <h4>{{ t('tools.desc.title') }}</h4>
              <p>{{ selectedTool.description }}</p>
            </div>

            <div class="schema-card">
              <h4>{{ t('tools.schema.title') }}</h4>
              <pre class="schema-pre">{{ JSON.stringify(selectedTool.inputSchema || {}, null, 2) }}</pre>
            </div>
          </div>
          <p v-else class="mesh-empty">{{ t('tools.empty.no_tools') }}</p>

          <div class="probe-card">
            <div class="probe-header">
              <div>
                <h4>{{ t('tools.probe.title') }}</h4>
                <small>{{ t('tools.probe.subtitle') }}</small>
              </div>
              <button class="btn-run-probe" :disabled="probing" @click="runToolProbe">
                <AppIcon name="refresh" :size="13" />
                <span>{{ probing ? t('tools.probe.running') : t('tools.probe.btn.run') }}</span>
              </button>
            </div>
            <div v-if="probeResult" :class="['probe-result-box', { success: probeResult.ok, error: !probeResult.ok }]">
              <div class="probe-result-top">
                <span class="status-badge">{{ probeResult.status }}</span>
                <span class="latency-badge">{{ probeResult.latency_ms }} ms</span>
              </div>
              <pre class="probe-detail">{{ probeResult.detail }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mcp-mesh-page {
  min-height: 100%;
  background: var(--canvas);
  color: var(--ink);
  padding: 28px 40px;
  overflow-y: auto;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
}

.mesh-container {
  max-width: 1150px;
  margin: 0 auto;
}

.mesh-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 20px;
}

.mesh-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
}

.mesh-notice {
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid rgba(255, 59, 48, 0.3);
  border-radius: 8px;
  background: rgba(255, 59, 48, 0.08);
  color: var(--ink);
  font-size: 13px;
}

.mesh-empty {
  padding: 18px;
  color: var(--muted);
  font-size: 13px;
}

.btn-refresh {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 16px;
  border-radius: 8px;
  border: none;
  background: var(--ink);
  color: var(--surface);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-refresh:hover { opacity: 0.88; }
.btn-refresh:disabled { opacity: 0.5; cursor: wait; }

/* Live service grid */
.services-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.service-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-sm);
}
.service-card:hover {
  background: var(--surface-soft);
  border-color: var(--line-strong);
}
.service-card.active {
  background: var(--surface);
  border-color: var(--primary);
  box-shadow: 0 0 0 1px var(--primary), 0 2px 8px rgba(0, 113, 227, 0.15);
}
.service-card.offline {
  opacity: 0.5;
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.type-pill {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 4px;
}
.type-pill.query { background: rgba(0, 113, 227, 0.12); color: var(--primary); }
.type-pill.action { background: rgba(175, 82, 222, 0.14); color: #a855f7; }

.latency-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-family: ui-monospace, monospace;
  color: var(--muted);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ff3b30;
}
.status-dot.online {
  background: #34c759;
}
.status-dot.pulsing {
  animation: pulse 1.2s infinite ease-in-out;
}
@keyframes pulse {
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.4); opacity: 0.5; }
  100% { transform: scale(1); opacity: 1; }
}

.service-name {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.service-id {
  font-size: 11px;
  color: var(--muted);
  font-family: ui-monospace, monospace;
  margin-bottom: 16px;
}

.service-footer {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: var(--muted);
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
.arrow-icon { color: var(--muted); }

/* Detail Split */
.mesh-detail-split {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  overflow: hidden;
  min-height: 480px;
  box-shadow: var(--shadow-sm);
}

.tools-sidebar {
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  background: var(--surface-soft);
}
.tools-sidebar__header {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.tools-sidebar__header h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.count-tag {
  background: var(--line);
  color: var(--ink);
  padding: 2px 6px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

.tools-list {
  flex: 1;
  overflow-y: auto;
}
.tool-item {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  background: var(--surface);
  transition: background 0.12s;
}
.tool-item:hover { background: var(--surface-soft); }
.tool-item.active { background: var(--surface); border-left: 3px solid var(--primary); }

.tool-item-name {
  font-size: 13px;
  font-weight: 600;
  font-family: ui-monospace, monospace;
  color: var(--ink);
  margin-bottom: 3px;
}
.tool-item.active .tool-item-name {
  color: var(--primary);
}
.tool-item-desc {
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Tool Inspector */
.tool-inspector {
  padding: 24px;
  overflow-y: auto;
  background: var(--surface);
}
.inspector-header {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 20px;
}
.inspector-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.inspector-title h3 {
  margin: 0;
  font-size: 18px;
  color: var(--ink);
  font-family: "SF Mono", Menlo, monospace;
}
.inspector-url {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
.inspector-url code {
  color: var(--primary);
  font-family: ui-monospace, monospace;
}

.desc-card, .schema-card {
  margin-bottom: 20px;
}
.desc-card h4, .schema-card h4 {
  margin: 0 0 8px;
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.desc-card p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}

.schema-pre {
  margin: 0;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink);
  overflow-x: auto;
}

/* Connection probe */
.probe-card {
  margin-top: 20px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 16px;
}

.probe-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.probe-header h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.probe-header small {
  font-size: 11px;
  color: var(--muted);
}

.btn-run-probe {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #34c759;
  color: #ffffff;
  border: none;
  font-size: 12px;
  font-weight: 500;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.btn-run-probe:hover:not(:disabled) {
  background: #2db34e;
}

.btn-run-probe:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.probe-result-box {
  margin-top: 12px;
  border-radius: 8px;
  padding: 12px;
}

.probe-result-box.success {
  background: rgba(52, 199, 89, 0.12);
  border: 1px solid rgba(52, 199, 89, 0.3);
}

.probe-result-box.error {
  background: rgba(255, 59, 48, 0.12);
  border: 1px solid rgba(255, 59, 48, 0.3);
}

.probe-result-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.status-badge {
  font-size: 11px;
  font-weight: 600;
  color: #34c759;
}

.probe-result-box.error .status-badge {
  color: #ff3b30;
}

.probe-detail {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  color: var(--ink);
}
</style>
