<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import { useI18n } from '../composables/useI18n'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi
const { t, locale } = useI18n()

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

interface SkillItem {
  name: string
  description: string
  path: string
  content: string
}

const skills = ref<SkillItem[]>([])
const selectedSkill = ref<SkillItem | null>(null)
const editingPrompt = ref('')
const savingSkill = ref(false)
const skillMessage = ref('')

async function loadSkills() {
  try {
    const list = await api.getSkills()
    skills.value = list || []
    if (skills.value.length > 0 && !selectedSkill.value) {
      selectSkill(skills.value[0])
    }
  } catch (err: any) {
    console.warn('Failed to load skills/prompts:', err)
  }
}

function selectSkill(s: SkillItem) {
  selectedSkill.value = s
  editingPrompt.value = s.content
  skillMessage.value = ''
}

async function saveSkillPrompt() {
  if (!selectedSkill.value) return
  savingSkill.value = true
  skillMessage.value = ''
  try {
    await api.saveSkill(selectedSkill.value.name, editingPrompt.value)
    selectedSkill.value.content = editingPrompt.value
    skillMessage.value = `人设/Prompt【${selectedSkill.value.name}】已保存！秒级热重载生效。`
    setTimeout(() => { skillMessage.value = ''; }, 4000)
  } catch (err: any) {
    errorMessage.value = `保存 Prompt 失败: ${err.message}`
  } finally {
    savingSkill.value = false
  }
}

onMounted(() => {
  loadSettings()
  loadSkills()
})
</script>

<template>
  <div class="model-studio">
    <div class="studio-container">
      <header class="studio-header">
        <div class="header-left">
          <h2>{{ t('model.title') }}</h2>
        </div>
        <div class="header-actions">
          <button class="btn-test" :disabled="testing" @click="testConnection">
            <AppIcon name="activity" :size="15" />
            <span>{{ testing ? t('model.btn.testing') : t('model.btn.test') }}</span>
          </button>
          <button class="btn-save" :disabled="loading" @click="saveSettings">
            <AppIcon name="check" :size="15" />
            <span>{{ loading ? t('model.btn.saving') : t('model.btn.save') }}</span>
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
            <strong>{{ testResult.ok ? t('model.test.success') : t('model.test.failed') }}</strong>
            <span class="status-code">HTTP {{ testResult.status_code }}</span>
          </div>
          <div class="test-latency">
            <span>{{ t('model.test.latency') }}</span>
            <strong>{{ testResult.latency_ms }} ms</strong>
          </div>
        </div>
        <div class="test-detail">
          <span>{{ testResult.message }}</span>
          <div v-if="testResult.sample_text" class="sample-box">
            <small>{{ t('model.test.sample') }}</small>
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
            <h3>{{ t('model.card.gateway') }}</h3>
          </div>

          <div class="form-group">
            <label>{{ t('model.provider.label') }}</label>
            <select v-model="provider" class="studio-select">
              <option value="openai">OpenAI 兼容接口 (Tokener / OpenAI / 代理网关)</option>
              <option value="azure">Azure OpenAI</option>
              <option value="anthropic">Anthropic Claude</option>
              <option value="custom">自定义私有网关</option>
            </select>
            <small>选择底层模型协议，默认采用标准 OpenAI Responses 协议。</small>
          </div>

          <div class="form-group">
            <label>{{ t('model.base_url.label') }}</label>
            <input
              v-model="baseUrl"
              type="text"
              class="studio-input"
              placeholder="https://api.tokener.dev/v1"
            />
            <small>例如 <code>https://api.tokener.dev/v1</code> 或 <code>https://api.openai.com/v1</code></small>
          </div>

          <div class="form-group">
            <label>{{ t('model.name.label') }}</label>
            <input
              v-model="model"
              type="text"
              class="studio-input"
              placeholder="gpt-5.6-terra"
            />
            <small>Pi Agent 当前推荐模型：<code>gpt-5.6-terra</code>、<code>gpt-5.5</code>、<code>gpt-4o</code></small>
          </div>

          <div class="form-group">
            <label>{{ t('model.key.label') }}</label>
            <div class="key-input-row">
              <input
                v-if="!showKey"
                type="password"
                v-model="apiKey"
                class="studio-input key-input"
                :placeholder="hasKey ? t('model.key.configured') + apiKeyMasked + ' ' + t('model.key.override') : 'sk-...'"
              />
              <input
                v-else
                type="text"
                v-model="apiKey"
                class="studio-input key-input"
                placeholder="sk-..."
              />
              <button class="btn-secondary" @click="showKey = !showKey">
                {{ showKey ? (locale === 'zh-CN' ? '隐藏' : 'Hide') : (locale === 'zh-CN' ? '明文' : 'Show') }}
              </button>
            </div>
            <small>{{ t('model.key.hint') }}</small>
          </div>
        </section>

        <!-- Advanced Runtime Card -->
        <section class="studio-card">
          <div class="card-title">
            <AppIcon name="sliders" :size="18" />
            <h3>{{ t('model.card.runtime') }}</h3>
          </div>

          <div class="form-group">
            <label>{{ t('model.reasoning.label') }}</label>
            <div class="toggle-row">
              <input type="checkbox" id="reasoningToggle" v-model="reasoning" class="studio-checkbox" />
              <label for="reasoningToggle" class="checkbox-label">
                {{ t('model.reasoning.desc') }}
              </label>
            </div>
          </div>

          <div class="form-group">
            <label>{{ t('model.timeout.label') }}{{ timeoutSeconds }}{{ t('model.timeout.sec') }}</label>
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

      <!-- Prompt & Persona Studio -->
      <section class="studio-card prompt-card" v-if="skills.length > 0">
        <div class="card-title-row">
          <div class="card-title">
            <AppIcon name="spark" :size="18" />
            <div>
              <h3>{{ t('model.prompt.title') }}</h3>
              <small>{{ t('model.prompt.subtitle') }}</small>
            </div>
          </div>
          <div class="prompt-actions">
            <span v-if="skillMessage" class="skill-toast">{{ skillMessage }}</span>
            <button class="btn-save-prompt" :disabled="savingSkill" @click="saveSkillPrompt">
              <AppIcon name="check" :size="14" />
              <span>{{ savingSkill ? t('model.prompt.saving') : t('model.prompt.btn.save') }}</span>
            </button>
          </div>
        </div>

        <div class="prompt-tabs">
          <button
            v-for="s in skills"
            :key="s.name"
            :class="['prompt-tab-btn', { active: selectedSkill?.name === s.name }]"
            @click="selectSkill(s)"
          >
            <strong>{{ s.name }}</strong>
            <small>{{ s.description || '业务推理规约' }}</small>
          </button>
        </div>

        <div class="prompt-editor-area" v-if="selectedSkill">
          <div class="editor-meta">
            <span>文件路径: <code>{{ selectedSkill.path }}</code></span>
            <span>字符数: {{ editingPrompt.length }}</span>
          </div>
          <textarea
            v-model="editingPrompt"
            class="prompt-textarea"
            rows="16"
            placeholder="输入 Markdown 格式的 System Prompt 或排障人设准则..."
          ></textarea>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.model-studio {
  min-height: 100%;
  background: var(--surface);
  color: var(--ink);
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
  align-items: center;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 20px;
}

.studio-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
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
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-test:hover {
  background: var(--surface-hover);
}

.btn-save {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 18px;
  border-radius: 8px;
  border: none;
  background: var(--ink);
  color: var(--surface);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-save:hover {
  opacity: 0.9;
}

.notice-banner {
  padding: 12px 18px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 24px;
}
.notice-banner.success {
  background: var(--green-soft);
  border: 1px solid rgba(52, 199, 89, 0.25);
  color: var(--green);
}
.notice-banner.error {
  background: var(--red-soft);
  border: 1px solid rgba(255, 59, 48, 0.25);
  color: var(--red);
}

/* Test Card */
.test-card {
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 24px;
  border: 1px solid transparent;
}
.test-card.success {
  background: var(--green-soft);
  border-color: rgba(52, 199, 89, 0.3);
}
.test-card.failed {
  background: var(--red-soft);
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
.test-card.success .status-indicator { background: var(--green); }
.test-card.failed .status-indicator { background: var(--red); }

.status-code {
  font-size: 11px;
  font-family: ui-monospace, monospace;
  background: var(--surface-soft);
  color: var(--ink);
  padding: 2px 6px;
  border-radius: 4px;
}
.test-latency {
  font-size: 12px;
  color: var(--muted);
}
.test-latency strong {
  color: var(--ink);
  font-family: ui-monospace, monospace;
  font-size: 13px;
  margin-left: 4px;
}

.test-detail {
  font-size: 12px;
  color: var(--ink-soft);
}
.sample-box {
  margin-top: 8px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  padding: 8px 12px;
  border-radius: 6px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.sample-box small { color: var(--muted); }
.sample-box code { color: var(--green); font-family: ui-monospace, monospace; }

/* Grid */
.studio-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.studio-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-sm);
}

.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  color: var(--ink);
}
.card-title h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.form-group {
  margin-bottom: 18px;
}
.form-group label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 6px;
}
.form-group small {
  display: block;
  font-size: 11px;
  color: var(--muted);
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
  border: 1px solid var(--line-strong);
  background: var(--surface-soft);
  color: var(--ink);
  font-size: 13px;
  outline: none;
  font-family: inherit;
  transition: border-color 0.15s;
}
.studio-input:focus, .studio-select:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-soft);
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
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
  font-size: 12px;
  cursor: pointer;
}
.btn-secondary:hover {
  background: var(--surface-hover);
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
  color: var(--ink-soft) !important;
  margin-bottom: 0 !important;
  cursor: pointer;
}

.studio-range {
  width: 100%;
  accent-color: var(--blue);
}

.runtime-status-box {
  margin-top: 24px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px 16px;
}
.runtime-status-box h4 {
  margin: 0 0 10px;
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.status-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}
.status-row:last-child {
  border-bottom: none;
}
.status-row span { color: var(--muted); }
.status-row strong { color: var(--ink); }

/* Prompt & Persona Studio */
.prompt-card {
  margin-top: 24px;
}

.card-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.card-title-row .card-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 0;
}

.card-title-row h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.card-title-row small {
  font-size: 11px;
  color: var(--muted);
}

.prompt-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.skill-toast {
  font-size: 12px;
  color: var(--green);
  font-weight: 500;
}

.btn-save-prompt {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--blue);
  color: #fff;
  border: none;
  font-size: 12px;
  font-weight: 500;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.btn-save-prompt:hover:not(:disabled) {
  opacity: 0.9;
}

.btn-save-prompt:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.prompt-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.prompt-tab-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 8px 14px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
  min-width: 140px;
}

.prompt-tab-btn:hover {
  background: var(--surface-hover);
}

.prompt-tab-btn.active {
  background: var(--surface);
  border-color: var(--blue);
  box-shadow: 0 1px 4px var(--blue-soft);
}

.prompt-tab-btn strong {
  font-size: 12px;
  color: var(--ink);
}

.prompt-tab-btn small {
  font-size: 10px;
  color: var(--muted);
}

.prompt-editor-area {
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}

.editor-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 8px;
}

.editor-meta code {
  background: var(--surface);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--ink);
  border: 1px solid var(--line);
}

.prompt-textarea {
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 12px;
  background: var(--surface);
  color: var(--ink);
  resize: vertical;
  box-sizing: border-box;
}

.prompt-textarea:focus {
  border-color: var(--blue);
  outline: none;
}
</style>
