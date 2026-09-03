<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

const provider = ref('openai')
const baseUrl = ref('https://api.tokener.dev/v1')
const model = ref('gpt-5.6-terra')
const apiKey = ref('')
const apiKeyMasked = ref('********')
const hasKey = ref(false)
const showKey = ref(false)
const reasoning = ref(true)
const timeoutSeconds = ref(120)

const loading = ref(false)
const testing = ref(false)
const testResult = ref<{
  ok: boolean
  status_code: number
  latency_ms: number
  message: string
  sample_text: string
} | null>(null)
const saveMessage = ref('')
const errorMessage = ref('')

async function loadSettings() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await api.getModelSettings()
    provider.value = data.provider || 'openai'
    baseUrl.value = data.base_url || 'https://api.tokener.dev/v1'
    model.value = data.model || 'gpt-5.6-terra'
    apiKeyMasked.value = data.api_key_masked || '********'
    hasKey.value = data.has_key || false
    reasoning.value = data.reasoning ?? true
    timeoutSeconds.value = data.timeout_seconds || 120
  } catch (err: any) {
    errorMessage.value = err.message || '获取模型配置失败'
  } finally {
    loading.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    const res = await api.testModelConnection({
      base_url: baseUrl.value,
      model: model.value,
      api_key: apiKey.value || undefined,
    })
    testResult.value = res
  } catch (err: any) {
    testResult.value = {
      ok: false,
      status_code: 500,
      latency_ms: 0,
      message: err.message || '连接测试异常',
      sample_text: '',
    }
  } finally {
    testing.value = false
  }
}

async function saveSettings() {
  loading.value = true
  saveMessage.value = ''
  errorMessage.value = ''
  try {
    const payload: any = {
      provider: provider.value,
      base_url: baseUrl.value,
      model: model.value,
      reasoning: reasoning.value,
      timeout_seconds: timeoutSeconds.value,
    }
    if (apiKey.value.trim()) {
      payload.api_key = apiKey.value.trim()
    }
    await api.saveModelSettings(payload)
    saveMessage.value = '模型配置已保存并立即生效！Pi Agent 无需重启即可应用新模型。'
    apiKey.value = ''
    await loadSettings()
    setTimeout(() => { saveMessage.value = ''; }, 5000)
  } catch (err: any) {
    errorMessage.value = err.message || '保存配置失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadSettings()
})
</script>

<template>
  <div class="model-studio">
    <div class="studio-container">
      <header class="studio-header">
        <div class="header-left">
          <div class="badge-tag">Model Studio</div>
          <h2>模型配置与实时测速中心</h2>
          <p>动态调整 Pi Agent 的上游大模型端点、推理深度与凭据，支持热更新与现场延迟诊断。</p>
        </div>
        <div class="header-actions">
          <button class="btn-test" :disabled="testing" @click="testConnection">
            <AppIcon name="activity" :size="15" />
            <span>{{ testing ? '测速中...' : '测试连通性 (Ping Test)' }}</span>
          </button>
          <button class="btn-save" :disabled="loading" @click="saveSettings">
            <AppIcon name="check" :size="15" />
            <span>{{ loading ? '保存中...' : '保存并热生效' }}</span>
          </button>
        </div>
      </header>

      <!-- Alert Notices -->
      <div v-if="saveMessage" class="notice-banner success">{{ saveMessage }}</div>
      <div v-if="errorMessage" class="notice-banner error">{{ errorMessage }}</div>

      <!-- Ping Test Result Card (Apple-style pill alert) -->
      <div v-if="testResult" :class="['test-card', { success: testResult.ok, failed: !testResult.ok }]">
        <div class="test-header">
          <div class="test-status">
            <span class="status-indicator"></span>
            <strong>{{ testResult.ok ? '模型连通测试通过' : '模型连通失败' }}</strong>
            <span class="status-code">HTTP {{ testResult.status_code }}</span>
          </div>
          <div class="test-latency">
            <span>首字延迟:</span>
            <strong>{{ testResult.latency_ms }} ms</strong>
          </div>
        </div>
        <div class="test-detail">
          <span>{{ testResult.message }}</span>
          <div v-if="testResult.sample_text" class="sample-box">
            <small>回复样本:</small>
            <code>{{ testResult.sample_text }}</code>
          </div>
        </div>
      </div>

      <!-- Main Form Grid -->
      <div class="studio-grid">
        <!-- Configuration Card -->
        <section class="studio-card">
          <div class="card-title">
            <AppIcon name="spark" :size="18" />
            <h3>模型网关参数</h3>
          </div>

          <div class="form-group">
            <label>Provider 服务商类型</label>
            <select v-model="provider" class="studio-select">
              <option value="openai">OpenAI 兼容接口 (Tokener / OpenAI / 代理网关)</option>
              <option value="azure">Azure OpenAI</option>
              <option value="anthropic">Anthropic Claude</option>
              <option value="custom">自定义私有网关</option>
            </select>
            <small>选择底层模型协议，默认采用标准 OpenAI Responses 协议。</small>
          </div>

          <div class="form-group">
            <label>API Base URL (网关地址)</label>
            <input
              v-model="baseUrl"
              type="text"
              class="studio-input"
              placeholder="https://api.tokener.dev/v1"
            />
            <small>例如 <code>https://api.tokener.dev/v1</code> 或 <code>https://api.openai.com/v1</code></small>
          </div>

          <div class="form-group">
            <label>Model Identifier (模型名称)</label>
            <input
              v-model="model"
              type="text"
              class="studio-input"
              placeholder="gpt-5.6-terra"
            />
            <small>Pi Agent 当前推荐模型：<code>gpt-5.6-terra</code>、<code>gpt-5.5</code>、<code>gpt-4o</code></small>
          </div>

          <div class="form-group">
            <label>API Key (访问密钥)</label>
            <div class="key-input-row">
              <input
                v-if="!showKey"
                type="password"
                v-model="apiKey"
                class="studio-input key-input"
                :placeholder="hasKey ? '已配置: ' + apiKeyMasked + ' (输入新 Key 以覆盖)' : 'sk-...'"
              />
              <input
                v-else
                type="text"
                v-model="apiKey"
                class="studio-input key-input"
                placeholder="sk-..."
              />
              <button class="btn-secondary" @click="showKey = !showKey">
                {{ showKey ? '隐藏' : '明文' }}
              </button>
            </div>
            <small>密钥安全加密保存在生产数据库，绝不在前端明文暴露。</small>
          </div>
        </section>

        <!-- Advanced Runtime Card -->
        <section class="studio-card">
          <div class="card-title">
            <AppIcon name="sliders" :size="18" />
            <h3>推理与超时策略</h3>
          </div>

          <div class="form-group">
            <label>深度思维链 (Reasoning Mode)</label>
            <div class="toggle-row">
              <input type="checkbox" id="reasoningToggle" v-model="reasoning" class="studio-checkbox" />
              <label for="reasoningToggle" class="checkbox-label">
                允许模型产生 <code>thinking</code> 思考块，并在管理后台提供可折叠回放
              </label>
            </div>
          </div>

          <div class="form-group">
            <label>单步推理超时阈值: {{ timeoutSeconds }} 秒</label>
            <input
              v-model.number="timeoutSeconds"
              type="range"
              min="30"
              max="300"
              step="10"
              class="studio-range"
            />
            <small>针对复杂 SQL 排查与 SigNoz 日志聚合，建议保留 120 秒以上。</small>
          </div>

          <div class="runtime-status-box">
            <h4>当前生效环境</h4>
            <div class="status-row">
              <span>架构内核</span>
              <strong>Pi Agent Framework</strong>
            </div>
            <div class="status-row">
              <span>执行模式</span>
              <strong>单工作流自主推理 (Single Session Worker)</strong>
            </div>
            <div class="status-row">
              <span>动态存储</span>
              <strong>PostgreSQL system_settings (热更秒级生效)</strong>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.model-studio {
  min-height: 100%;
  background: #ffffff;
  color: #1d1d1f;
  padding: 28px 40px;
  overflow-y: auto;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
}

.studio-container {
  max-width: 1050px;
  margin: 0 auto;
}

.studio-header {
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
  color: #8b5cf6;
  background: rgba(175, 82, 222, 0.1);
  padding: 3px 8px;
  border-radius: 6px;
  margin-bottom: 8px;
}

.studio-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -0.02em;
}

.studio-header p {
  margin: 0;
  color: #86868b;
  font-size: 13px;
  max-width: 600px;
  line-height: 1.5;
}

.header-actions {
  display: flex;
  gap: 12px;
}

.btn-test {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 16px;
  border-radius: 8px;
  border: 1px solid #d2d2d7;
  background: #ffffff;
  color: #1d1d1f;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-test:hover {
  background: #f5f5f7;
}

.btn-save {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 18px;
  border-radius: 8px;
  border: none;
  background: #000000;
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-save:hover {
  background: #27272a;
}

.notice-banner {
  padding: 12px 18px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 24px;
}
.notice-banner.success {
  background: rgba(52, 199, 89, 0.12);
  border: 1px solid rgba(52, 199, 89, 0.25);
  color: #059669;
}
.notice-banner.error {
  background: rgba(255, 59, 48, 0.1);
  border: 1px solid rgba(255, 59, 48, 0.25);
  color: #dc2626;
}

/* Test Card */
.test-card {
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 24px;
  border: 1px solid transparent;
}
.test-card.success {
  background: rgba(52, 199, 89, 0.08);
  border-color: rgba(52, 199, 89, 0.3);
}
.test-card.failed {
  background: rgba(255, 59, 48, 0.08);
  border-color: rgba(255, 59, 48, 0.3);
}

.test-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.test-status {
  display: flex;
  align-items: center;
  gap: 8px;
}
.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.test-card.success .status-indicator { background: #34c759; }
.test-card.failed .status-indicator { background: #ff3b30; }

.status-code {
  font-size: 11px;
  font-family: ui-monospace, monospace;
  background: rgba(0, 0, 0, 0.06);
  color: #1d1d1f;
  padding: 2px 6px;
  border-radius: 4px;
}
.test-latency {
  font-size: 12px;
  color: #86868b;
}
.test-latency strong {
  color: #1d1d1f;
  font-family: ui-monospace, monospace;
  font-size: 13px;
  margin-left: 4px;
}

.test-detail {
  font-size: 12px;
  color: #515154;
}
.sample-box {
  margin-top: 8px;
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  padding: 8px 12px;
  border-radius: 6px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.sample-box small { color: #86868b; }
.sample-box code { color: #059669; font-family: ui-monospace, monospace; }

/* Grid */
.studio-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.studio-card {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-sm);
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  color: #1d1d1f;
}
.card-title h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1d1d1f;
}

.form-group {
  margin-bottom: 18px;
}
.form-group label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 6px;
}
.form-group small {
  display: block;
  font-size: 11px;
  color: #86868b;
  margin-top: 5px;
}
.form-group code {
  color: #8b5cf6;
}

.studio-input, .studio-select {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid #d2d2d7;
  background: #ffffff;
  color: #1d1d1f;
  font-size: 13px;
  outline: none;
  font-family: inherit;
  transition: border-color 0.15s;
}
.studio-input:focus, .studio-select:focus {
  border-color: #0071e3;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}

.key-input-row {
  display: flex;
  gap: 8px;
}
.key-input {
  flex: 1;
}

.btn-secondary {
  height: 38px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid #d2d2d7;
  background: #ffffff;
  color: #1d1d1f;
  font-size: 12px;
  cursor: pointer;
}
.btn-secondary:hover {
  background: #f5f5f7;
}

.toggle-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.studio-checkbox {
  width: 18px;
  height: 18px;
  cursor: pointer;
}
.checkbox-label {
  font-size: 12px !important;
  font-weight: 400 !important;
  color: #515154 !important;
  margin-bottom: 0 !important;
  cursor: pointer;
}

.studio-range {
  width: 100%;
  accent-color: #0071e3;
}

.runtime-status-box {
  margin-top: 24px;
  background: #fbfbfd;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 8px;
  padding: 14px 16px;
}
.runtime-status-box h4 {
  margin: 0 0 10px;
  font-size: 11px;
  color: #86868b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.status-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}
.status-row:last-child {
  border-bottom: none;
}
.status-row span { color: #86868b; }
.status-row strong { color: #1d1d1f; }
</style>
