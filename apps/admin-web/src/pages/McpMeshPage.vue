<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'

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
const totalTools = ref(0)
const loading = ref(false)
const selectedService = ref<McpService | null>(null)
const selectedTool = ref<any | null>(null)
const filterKeyword = ref('')

async function loadMesh() {
  loading.value = true
  try {
    const res = await api.getMcpMesh()
    services.value = res.services || []
    totalTools.value = res.total_tools || 0
    if (services.value.length > 0 && !selectedService.value) {
      selectedService.value = services.value[0]
      if (selectedService.value.tools.length > 0) {
        selectedTool.value = selectedService.value.tools[0]
      }
    }
  } catch (err: any) {
    console.error('Failed to load MCP mesh:', err)
  } finally {
    loading.value = false
  }
}

function selectService(s: McpService) {
  selectedService.value = s
  selectedTool.value = s.tools.length > 0 ? s.tools[0] : null
}

function selectTool(t: any) {
  selectedTool.value = t
}

onMounted(() => {
  loadMesh()
})
</script>

<template>
  <div class="mcp-mesh-page">
    <div class="mesh-container">
      <header class="mesh-header">
        <div class="header-left">
          <div class="badge-tag">Tool Bus</div>
          <h2>全能力 MCP 工具总线 (MCP Mesh)</h2>
          <p>聚合排查检索类与前台投递类 7 大服务，提供工具清单、Schema 参数定义与实时连通性探测。</p>
        </div>
        <div class="header-actions">
          <button class="btn-refresh" :disabled="loading" @click="loadMesh">
            <AppIcon name="refresh" :size="15" />
            <span>{{ loading ? '探测中...' : '刷新服务状态' }}</span>
          </button>
        </div>
      </header>

      <!-- 7 Services Grid Cards -->
      <div class="services-grid">
        <div
          v-for="s in services"
          :key="s.id"
          :class="['service-card', { active: selectedService?.id === s.id, offline: !s.online }]"
          @click="selectService(s)"
        >
          <div class="card-top">
            <span :class="['type-pill', s.service_type]">{{ s.service_type === 'action' ? 'ACTION 投递' : 'QUERY 检索' }}</span>
            <div class="latency-badge">
              <span :class="['status-dot', { online: s.online }]"></span>
              <span>{{ s.online ? s.latency_ms + 'ms' : '离线' }}</span>
            </div>
          </div>
          <h4 class="service-name">{{ s.label }}</h4>
          <code class="service-id">{{ s.id }}</code>
          <div class="service-footer">
            <span class="tools-count">{{ s.tool_count }} 个可用工具</span>
            <span class="arrow-icon">→</span>
          </div>
        </div>
      </div>

      <!-- Detail & Playground Area -->
      <div class="mesh-detail-split" v-if="selectedService">
        <!-- Left: Tool List of Selected Service -->
        <div class="tools-sidebar">
          <div class="tools-sidebar__header">
            <h4>{{ selectedService.label }} 工具列表</h4>
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
              <div class="tool-item-desc">{{ tool.description || '无描述' }}</div>
            </div>
          </div>
        </div>

        <!-- Right: Tool Schema & Inspector -->
        <div class="tool-inspector" v-if="selectedTool">
          <div class="inspector-header">
            <div class="inspector-title">
              <h3>{{ selectedTool.name }}</h3>
              <span :class="['type-pill', selectedService.service_type]">
                {{ selectedService.id }}
              </span>
            </div>
            <div class="inspector-url">
              <small>端点:</small>
              <code>{{ selectedService.endpoint }}</code>
            </div>
          </div>

          <div class="inspector-body">
            <div class="desc-card">
              <h4>功能描述</h4>
              <p>{{ selectedTool.description }}</p>
            </div>

            <div class="schema-card">
              <h4>Input Schema (入参定义)</h4>
              <pre class="schema-pre">{{ JSON.stringify(selectedTool.inputSchema || {}, null, 2) }}</pre>
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
  background: #ffffff;
  color: #1d1d1f;
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
  align-items: flex-end;
  padding-bottom: 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  margin-bottom: 24px;
}

.badge-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #0071e3;
  background: rgba(0, 113, 227, 0.1);
  padding: 3px 8px;
  border-radius: 6px;
  margin-bottom: 8px;
}

.mesh-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -0.02em;
}

.mesh-header p {
  margin: 0;
  color: #86868b;
  font-size: 13px;
  max-width: 650px;
  line-height: 1.5;
}

.btn-refresh {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 16px;
  border-radius: 8px;
  border: none;
  background: #000000;
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-refresh:hover { background: #27272a; }

/* 7 Services Grid */
.services-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.service-card {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-sm);
}
.service-card:hover {
  background: #f5f5f7;
  border-color: rgba(0, 0, 0, 0.15);
}
.service-card.active {
  background: #ffffff;
  border-color: #0071e3;
  box-shadow: 0 0 0 1px #0071e3, 0 2px 8px rgba(0, 113, 227, 0.1);
}
.service-card.offline {
  opacity: 0.6;
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
.type-pill.query { background: rgba(0, 113, 227, 0.1); color: #0071e3; }
.type-pill.action { background: rgba(175, 82, 222, 0.1); color: #8b5cf6; }

.latency-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-family: ui-monospace, monospace;
  color: #86868b;
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

.service-name {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: #1d1d1f;
}
.service-id {
  font-size: 11px;
  color: #86868b;
  font-family: ui-monospace, monospace;
  margin-bottom: 16px;
}

.service-footer {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: #6e6e73;
  padding-top: 10px;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
}
.arrow-icon { color: #86868b; }

/* Detail Split */
.mesh-detail-split {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  overflow: hidden;
  min-height: 480px;
  box-shadow: var(--shadow-sm);
}

.tools-sidebar {
  border-right: 1px solid rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  background: #fbfbfd;
}
.tools-sidebar__header {
  padding: 14px 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.tools-sidebar__header h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: #1d1d1f;
}
.count-tag {
  background: #f0f0f2;
  color: #515154;
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
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
  cursor: pointer;
  background: #ffffff;
  transition: background 0.12s;
}
.tool-item:hover { background: #f5f5f7; }
.tool-item.active { background: #ffffff; border-left: 3px solid #0071e3; }

.tool-item-name {
  font-size: 13px;
  font-weight: 600;
  font-family: ui-monospace, monospace;
  color: #1d1d1f;
  margin-bottom: 3px;
}
.tool-item.active .tool-item-name {
  color: #0071e3;
}
.tool-item-desc {
  font-size: 11px;
  color: #86868b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Tool Inspector */
.tool-inspector {
  padding: 24px;
  overflow-y: auto;
  background: #ffffff;
}
.inspector-header {
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
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
  color: #1d1d1f;
  font-family: "SF Mono", Menlo, monospace;
}
.inspector-url {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: #86868b;
}
.inspector-url code {
  color: #0071e3;
  font-family: ui-monospace, monospace;
}

.desc-card, .schema-card {
  margin-bottom: 20px;
}
.desc-card h4, .schema-card h4 {
  margin: 0 0 8px;
  font-size: 11px;
  color: #86868b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.desc-card p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: #333336;
}

.schema-pre {
  margin: 0;
  background: #f8f9fa;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 14px;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #1e293b;
  overflow-x: auto;
}
</style>
