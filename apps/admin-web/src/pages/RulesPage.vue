<script setup lang="ts">
import { inject, onMounted, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import type { ProcessingRules } from '../types'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

const rules = reactive<ProcessingRules>({
  saasTag: 'saas',
  assignee: '40685275477140',
  statuses: ['new', 'open'],
  humanAliases: ['human', '人工', '真人'],
  replyEnabled: true,
  footerLocale: 'en',
  footers: {},
})

const aliasDraft = ref('')
const saveState = ref<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('loading')
const revision = ref('—')
const snapshot = ref<Awaited<ReturnType<typeof api.getConfig>> | null>(null)
const errorMessage = ref('')
const statuses = ['new', 'open']

function applyConfig(next: Awaited<ReturnType<typeof api.getConfig>>) {
  snapshot.value = next
  revision.value = next.revision
  Object.assign(rules, {
    ...next.config.rules,
    statuses: [...(next.config.rules.statuses || ['new', 'open'])],
    humanAliases: [...(next.config.rules.humanAliases || ['human', '人工', '真人'])],
    footers: { ...next.config.rules.footers },
  })
  if (!rules.footers['en']) {
    rules.footers['en'] = 'This reply was sent by Dify AI Agent. If this did not resolve your issue, please reply "human" to connect with a support specialist.'
  }
}

async function loadRules(preserveError = false) {
  saveState.value = 'loading'
  if (!preserveError) errorMessage.value = ''
  try {
    applyConfig(await api.getConfig())
    saveState.value = 'idle'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取处理规则失败'
    saveState.value = 'error'
  }
}

function isConflict(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error && (error as { status?: unknown }).status === 409
}

function toggleStatus(status: string) {
  if (rules.statuses.includes(status)) {
    rules.statuses = rules.statuses.filter((item) => item !== status)
  } else {
    rules.statuses.push(status)
  }
}

function addAlias() {
  const alias = aliasDraft.value.trim()
  if (alias && !rules.humanAliases.includes(alias)) {
    rules.humanAliases.push(alias)
  }
  aliasDraft.value = ''
}

function removeAlias(alias: string) {
  rules.humanAliases = rules.humanAliases.filter((item) => item !== alias)
}

async function saveRules() {
  if (!snapshot.value) {
    await loadRules()
    return
  }
  saveState.value = 'saving'
  errorMessage.value = ''
  const nextRules: ProcessingRules = {
    saasTag: rules.saasTag,
    assignee: rules.assignee,
    statuses: [...rules.statuses],
    humanAliases: [...rules.humanAliases],
    replyEnabled: rules.replyEnabled,
    footerLocale: rules.footerLocale,
    footers: { ...rules.footers },
  }
  try {
    const saved = await api.saveConfig({
      expectedRevision: snapshot.value.revision,
      config: { ...snapshot.value.config, rules: nextRules },
    })
    applyConfig(saved)
    saveState.value = 'saved'
    setTimeout(() => { saveState.value = 'idle'; }, 4000)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存处理规则失败'
    saveState.value = 'error'
    if (isConflict(error)) {
      await loadRules(true)
      errorMessage.value = '配置版本已过期，已重新加载最新规则；请确认后再次保存。'
    }
  }
}

onMounted(() => {
  void loadRules()
})
</script>

<template>
  <div class="rules-page">
    <div class="rules-container">
      <!-- Header -->
      <header class="rules-header">
        <div class="header-left">
          <div class="badge-tag">Ingress & Guardrails</div>
          <h1>准入规则与人工保护</h1>
          <p>配置 Zendesk 事件准入过滤、人工优先抢答保护机制（模式 B）及回复策略。</p>
        </div>
        <div class="header-actions">
          <span v-if="saveState === 'saved'" class="save-toast">
            <AppIcon name="check" :size="14" />
            <span>已保存到版本 {{ revision }}</span>
          </span>
          <button
            type="button"
            class="btn-save"
            :disabled="saveState === 'saving' || saveState === 'loading'"
            @click="saveRules"
          >
            <AppIcon name="check" :size="15" />
            <span>{{ saveState === 'saving' ? '正在保存…' : '保存规则' }}</span>
          </button>
        </div>
      </header>

      <!-- Error Notice -->
      <div v-if="errorMessage" class="notice-card notice-card--error">
        <AppIcon name="alert" :size="16" />
        <div>
          <strong>规则保存异常</strong>
          <span>{{ errorMessage }}</span>
        </div>
      </div>

      <!-- Mode B Protection Callout -->
      <div class="protection-callout">
        <div class="callout-icon">
          <AppIcon name="shield" :size="20" />
        </div>
        <div class="callout-content">
          <div class="callout-title-row">
            <strong>人工优先保护机制已激活 (模式 B)</strong>
            <span class="pill-active">已保护</span>
          </div>
          <p>
            AI 仅处理<strong>【受托组是 SaaS】</strong>、状态是<strong>【new / open】</strong>且<strong>【未分配人工经办人（assignee 为空）】</strong>的工单。一旦分配给人工经办人（如 David Yang）或客户消息中命中人工转接词，AI 坚决不介入并保持静默。
          </p>
        </div>
      </div>

      <!-- Main Form Grid -->
      <div class="rules-grid">
        <!-- Card 1: Match Conditions -->
        <section class="apple-card">
          <div class="card-header">
            <div class="card-header-title">
              <span class="card-tag">Match Conditions</span>
              <h2>Webhook 标签与准入条件</h2>
              <p>当以下所有条件同时满足（AND 逻辑）时，工单才允许提交给 AI 推理。</p>
            </div>
            <span class="condition-badge">AND 条件</span>
          </div>

          <div class="card-body">
            <!-- SaaS Tag -->
            <div class="form-item">
              <div class="item-label-group">
                <label>SaaS 识别标签</label>
                <small>Zendesk 工单必须带有此标签，AI 才会作为候选任务捕获</small>
              </div>
              <div class="input-prefix-box">
                <span class="prefix">#</span>
                <input v-model="rules.saasTag" type="text" class="apple-input input-clean" placeholder="saas" />
              </div>
            </div>

            <!-- SaaS Assignee Group ID -->
            <div class="form-item">
              <div class="item-label-group">
                <label>SaaS 受托组 ID</label>
                <small>仅处理分配给指定 Zendesk 组的工单（当前：Dify SaaS 组）</small>
              </div>
              <div class="input-with-badge">
                <input v-model="rules.assignee" type="text" class="apple-input" placeholder="40685275477140" />
                <span class="inline-badge">Dify SaaS Group</span>
              </div>
            </div>

            <!-- Ticket Statuses -->
            <div class="form-item">
              <div class="item-label-group">
                <label>允许处理的工单状态</label>
                <small>工单当前状态必须处于选中的列表中</small>
              </div>
              <div class="status-selector">
                <div
                  v-for="st in statuses"
                  :key="st"
                  class="status-choice"
                  :class="{ active: rules.statuses.includes(st) }"
                  @click="toggleStatus(st)"
                >
                  <div class="choice-dot" />
                  <span class="choice-text">{{ st }}</span>
                  <AppIcon v-if="rules.statuses.includes(st)" name="check" :size="14" class="choice-check" />
                </div>
              </div>
            </div>

            <!-- Human Aliases -->
            <div class="form-item">
              <div class="item-label-group">
                <label>人工转接关键词 (Human Aliases)</label>
                <small>客户提问包含以下任一关键词时，立即停止 AI 答复并转人工经办</small>
              </div>
              <div class="tag-input-container">
                <div class="tag-chips">
                  <span v-for="alias in rules.humanAliases" :key="alias" class="tag-chip">
                    <span>{{ alias }}</span>
                    <button type="button" class="tag-remove" @click="removeAlias(alias)">×</button>
                  </span>
                  <input
                    v-model="aliasDraft"
                    type="text"
                    class="tag-editor-input"
                    placeholder="输入关键词按回车添加…"
                    @keydown.enter.prevent="addAlias"
                  />
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Card 2: Delivery Guardrails -->
        <section class="apple-card">
          <div class="card-header">
            <div class="card-header-title">
              <span class="card-tag">Delivery Guardrails</span>
              <h2>回复与交付策略</h2>
              <p>AI 生成答复后进入交付控制器，进行安全验证与统一前台投递。</p>
            </div>
            <AppIcon name="shield" :size="18" class="header-icon" />
          </div>

          <div class="card-body">
            <!-- Reply Toggle Switch -->
            <div class="toggle-card">
              <div class="toggle-info">
                <strong>启用客户可见回复 (Public Reply)</strong>
                <p>开启后通过 Zendesk Browser Relay 向客户前台发送真实消息；关闭时仅在后台推演生成草稿。</p>
              </div>
              <label class="apple-switch">
                <input v-model="rules.replyEnabled" type="checkbox" />
                <span class="slider round" />
              </label>
            </div>

            <!-- Language Policy -->
            <div class="form-item">
              <div class="item-label-group">
                <label>默认回复语言</label>
                <small>遵循统一纯英文回复原则，无论客户使用何种语言提问</small>
              </div>
              <div class="select-wrapper">
                <select v-model="rules.footerLocale" class="apple-select">
                  <option value="en">English (统一英文回复 · 推荐)</option>
                  <option value="zh-CN">简体中文 · zh-CN</option>
                  <option value="ja">日本語 · ja</option>
                </select>
              </div>
            </div>

            <!-- Localized Footer -->
            <div class="form-item">
              <div class="item-label-group">
                <label>官方免责与转人工尾注 (Footer)</label>
                <small>由交付控制器在回复末尾自动追加，模型无法篡改或重复插入</small>
              </div>
              <textarea
                v-model="rules.footers[rules.footerLocale]"
                class="apple-textarea"
                rows="3"
                placeholder="This reply was sent by Dify AI Agent..."
              />
            </div>
          </div>
        </section>
      </div>

      <!-- Card 3: Evaluation Preview Pipeline -->
      <section class="apple-card preview-card">
        <div class="card-header">
          <div class="card-header-title">
            <span class="card-tag">Deterministic Pipeline</span>
            <h2>准入与推理执行管道</h2>
          </div>
          <span class="version-label">当前生效修订: {{ revision }}</span>
        </div>
        <div class="pipeline-flow">
          <div class="flow-step">
            <span class="step-num">01</span>
            <strong>Zendesk 事件到达</strong>
            <small>Webhook / 轮询监听</small>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-step">
            <span class="step-num">02</span>
            <strong>准入与保护过滤</strong>
            <small>#{{ rules.saasTag }} · Group · 未分配人工</small>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-step">
            <span class="step-num">03</span>
            <strong>Pi Agent 证据推理</strong>
            <small>7 大 MCP 工具多轮推演</small>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-step flow-step--active">
            <span class="step-num">04</span>
            <strong>Browser Relay 交付</strong>
            <small>{{ rules.replyEnabled ? '已启用前台发送' : '静默模式 (只记日志)' }}</small>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.rules-page {
  min-height: 100%;
  background: #ffffff;
  color: #1d1d1f;
  padding: 28px 40px 48px;
  overflow-y: auto;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
}

.rules-container {
  max-width: 1080px;
  margin: 0 auto;
}

/* Header */
.rules-header {
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

.rules-header h1 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
  color: #1d1d1f;
  letter-spacing: -0.02em;
}

.rules-header p {
  margin: 0;
  color: #86868b;
  font-size: 13px;
  max-width: 650px;
  line-height: 1.5;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.save-toast {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #059669;
  background: rgba(52, 199, 89, 0.12);
  padding: 6px 12px;
  border-radius: 6px;
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
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  transition: background 0.15s;
}
.btn-save:hover { background: #27272a; }
.btn-save:disabled { opacity: 0.45; cursor: not-allowed; }

/* Notice Card */
.notice-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 10px;
  margin-bottom: 20px;
  font-size: 13px;
}
.notice-card--error {
  background: rgba(255, 59, 48, 0.1);
  border: 1px solid rgba(255, 59, 48, 0.25);
  color: #dc2626;
}

/* Mode B Callout */
.protection-callout {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 18px 22px;
  background: #fbfbfd;
  border: 1px solid rgba(0, 113, 227, 0.18);
  border-radius: 12px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 24px;
}
.callout-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: rgba(0, 113, 227, 0.1);
  color: #0071e3;
  display: grid;
  place-items: center;
  flex: 0 0 36px;
}
.callout-content {
  flex: 1;
}
.callout-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.callout-title-row strong {
  font-size: 14px;
  color: #1d1d1f;
}
.pill-active {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: #059669;
  background: rgba(52, 199, 89, 0.15);
  padding: 2px 7px;
  border-radius: 10px;
}
.callout-content p {
  margin: 0;
  font-size: 12px;
  color: #515154;
  line-height: 1.6;
}

/* Rules Grid */
.rules-grid {
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  gap: 20px;
  margin-bottom: 24px;
}

.apple-card {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 14px;
  box-shadow: var(--shadow-sm);
  padding: 24px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  margin-bottom: 20px;
}
.card-header-title {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.card-tag {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #86868b;
}
.card-header-title h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1d1d1f;
}
.card-header-title p {
  margin: 0;
  font-size: 12px;
  color: #86868b;
}

.condition-badge {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: #0071e3;
  background: rgba(0, 113, 227, 0.1);
  padding: 3px 8px;
  border-radius: 6px;
}
.header-icon {
  color: #86868b;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.item-label-group label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 2px;
}
.item-label-group small {
  display: block;
  font-size: 11px;
  color: #86868b;
}

/* Inputs */
.apple-input {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid #d2d2d7;
  border-radius: 8px;
  font-size: 13px;
  color: #1d1d1f;
  background: #ffffff;
  outline: none;
  transition: border-color 0.15s;
}
.apple-input:focus {
  border-color: #0071e3;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}

.input-prefix-box {
  display: flex;
  align-items: center;
  border: 1px solid #d2d2d7;
  border-radius: 8px;
  background: #ffffff;
  overflow: hidden;
}
.input-prefix-box:focus-within {
  border-color: #0071e3;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}
.prefix {
  padding: 0 12px;
  font-size: 13px;
  color: #86868b;
  font-weight: 600;
  background: #fbfbfd;
  border-right: 1px solid #e5e5e7;
  height: 38px;
  display: grid;
  place-items: center;
}
.input-clean {
  border: none !important;
  box-shadow: none !important;
}

.input-with-badge {
  position: relative;
  display: flex;
  align-items: center;
}
.inline-badge {
  position: absolute;
  right: 10px;
  font-size: 10px;
  color: #6e6e73;
  background: #f5f5f7;
  padding: 2px 8px;
  border-radius: 4px;
}

/* Status Selector */
.status-selector {
  display: flex;
  gap: 10px;
}
.status-choice {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border: 1px solid #d2d2d7;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
  transition: all 0.15s;
}
.status-choice:hover {
  background: #f5f5f7;
}
.status-choice.active {
  border-color: #0071e3;
  box-shadow: 0 0 0 1px #0071e3, 0 2px 6px rgba(0, 113, 227, 0.08);
}
.choice-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d2d2d7;
}
.status-choice.active .choice-dot {
  background: #34c759;
}
.choice-text {
  font-size: 13px;
  font-weight: 600;
  color: #1d1d1f;
  font-family: ui-monospace, monospace;
}
.choice-check {
  margin-left: auto;
  color: #0071e3;
}

/* Tag Chips */
.tag-input-container {
  border: 1px solid #d2d2d7;
  border-radius: 8px;
  background: #ffffff;
  padding: 8px 10px;
  transition: border-color 0.15s;
}
.tag-input-container:focus-within {
  border-color: #0071e3;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}
.tag-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #f5f5f7;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 12px;
  color: #1d1d1f;
}
.tag-remove {
  background: transparent;
  border: none;
  font-size: 14px;
  line-height: 1;
  color: #86868b;
  cursor: pointer;
  padding: 0 2px;
}
.tag-remove:hover { color: #dc2626; }
.tag-editor-input {
  flex: 1;
  min-width: 140px;
  border: none;
  outline: none;
  font-size: 12px;
  color: #1d1d1f;
  padding: 4px 0;
}

/* Toggle Card */
.toggle-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  background: #fbfbfd;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 10px;
}
.toggle-info strong {
  display: block;
  font-size: 13px;
  color: #1d1d1f;
  margin-bottom: 2px;
}
.toggle-info p {
  margin: 0;
  font-size: 11px;
  color: #86868b;
}

/* Apple Switch */
.apple-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
  flex: 0 0 44px;
}
.apple-switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: #e5e5ea;
  transition: .25s;
  border-radius: 24px;
}
.slider:before {
  position: absolute;
  content: "";
  height: 20px;
  width: 20px;
  left: 2px;
  bottom: 2px;
  background-color: white;
  transition: .25s;
  border-radius: 50%;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
input:checked + .slider { background-color: #34c759; }
input:checked + .slider:before { transform: translateX(20px); }

/* Select & Textarea */
.select-wrapper {
  position: relative;
}
.apple-select {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid #d2d2d7;
  border-radius: 8px;
  background: #ffffff;
  color: #1d1d1f;
  font-size: 13px;
  outline: none;
}
.apple-select:focus {
  border-color: #0071e3;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}

.apple-textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d2d2d7;
  border-radius: 8px;
  background: #ffffff;
  color: #1d1d1f;
  font-size: 12px;
  line-height: 1.5;
  font-family: ui-monospace, "SF Mono", monospace;
  outline: none;
  resize: vertical;
}
.apple-textarea:focus {
  border-color: #0071e3;
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}

/* Pipeline Flow */
.preview-card {
  margin-top: 4px;
}
.version-label {
  font-size: 11px;
  color: #86868b;
  font-family: ui-monospace, monospace;
}
.pipeline-flow {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-top: 10px;
}
.flow-step {
  flex: 1;
  background: #fbfbfd;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.flow-step--active {
  background: #ffffff;
  border-color: rgba(0, 113, 227, 0.3);
  box-shadow: 0 2px 8px rgba(0, 113, 227, 0.08);
}
.step-num {
  font-size: 10px;
  font-weight: 700;
  color: #0071e3;
}
.flow-step strong {
  font-size: 13px;
  color: #1d1d1f;
}
.flow-step small {
  font-size: 11px;
  color: #86868b;
}
.flow-arrow {
  color: #d2d2d7;
  font-size: 16px;
}
</style>
