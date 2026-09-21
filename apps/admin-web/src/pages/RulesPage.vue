<script setup lang="ts">
import { inject, onMounted, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import type { ControlMode, ProcessingRules, SafetySettings } from '../types'
import { useI18n } from '../composables/useI18n'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi
const { t, locale } = useI18n()

const controlMode = ref<ControlMode>('live')
const rules = reactive<ProcessingRules>({
  saasTag: 'saas',
  assignee: '40685275477140',
  statuses: ['new', 'open'],
  humanAliases: ['human', '人工', '真人', '退款', '投诉', '律师', '差评', '找经理'],
  replyEnabled: true,
  footerLocale: 'en',
  footers: {},
})

const safety = reactive<SafetySettings>({
  escalation_keywords: ['退款', '投诉', '律师', '人工', '差评', '找经理', '赔偿', '暂停服务', 'transfer to human', 'live agent'],
  auto_escalate_on_fail: true,
  max_retries: 2,
  ticket_blacklist: [4308],
  requester_blacklist: [],
  feishu_webhook_url: '',
})

const aliasDraft = ref('')
const blacklistDraft = ref('')
const saveState = ref<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('loading')
const revision = ref('—')
const snapshot = ref<Awaited<ReturnType<typeof api.getConfig>> | null>(null)
const errorMessage = ref('')
const statuses = ['new', 'open']

const feishuTesting = ref(false)
const feishuResult = ref<{
  ok: boolean
  status_code: number
  latency_ms: number
  message: string
} | null>(null)

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

async function loadAllSettings() {
  saveState.value = 'loading'
  errorMessage.value = ''
  try {
    const [cfg, modeRes, safeRes] = await Promise.all([
      api.getConfig(),
      api.getControlMode().catch(() => ({ mode: 'live' as ControlMode })),
      api.getSafetySettings().catch(() => null),
    ])
    applyConfig(cfg)
    controlMode.value = modeRes.mode
    if (safeRes) {
      Object.assign(safety, safeRes)
      if (safeRes.escalation_keywords?.length) {
        for (const kw of safeRes.escalation_keywords) {
          if (!rules.humanAliases.includes(kw)) {
            rules.humanAliases.push(kw)
          }
        }
      }
    }
    saveState.value = 'idle'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取规则与安全配置失败'
    saveState.value = 'error'
  }
}

async function switchControlMode(mode: ControlMode) {
  try {
    controlMode.value = mode
    await api.setControlMode(mode)
    saveState.value = 'saved'
    setTimeout(() => { saveState.value = 'idle'; }, 3000)
  } catch (err: any) {
    errorMessage.value = `切换运行模式失败: ${err.message}`
  }
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

function addBlacklistTicket() {
  const val = parseInt(blacklistDraft.value.trim(), 10)
  if (!isNaN(val) && val > 0 && !safety.ticket_blacklist.includes(val)) {
    safety.ticket_blacklist.push(val)
  }
  blacklistDraft.value = ''
}

function removeBlacklistTicket(id: number) {
  safety.ticket_blacklist = safety.ticket_blacklist.filter((item) => item !== id)
}

async function testFeishu() {
  feishuTesting.value = true
  feishuResult.value = null
  try {
    const res = await api.testFeishuWebhook({ webhook_url: safety.feishu_webhook_url })
    feishuResult.value = res
  } catch (err: any) {
    feishuResult.value = {
      ok: false,
      status_code: 500,
      latency_ms: 0,
      message: err.message || '飞书 Webhook 连通测试失败',
    }
  } finally {
    feishuTesting.value = false
  }
}

async function saveRules() {
  saveState.value = 'saving'
  errorMessage.value = ''
  try {
    safety.escalation_keywords = [...rules.humanAliases]
    const nextRules: ProcessingRules = {
      saasTag: rules.saasTag,
      assignee: rules.assignee,
      statuses: [...rules.statuses],
      humanAliases: [...rules.humanAliases],
      replyEnabled: rules.replyEnabled,
      footerLocale: rules.footerLocale,
      footers: { ...rules.footers },
    }
    const promises: Promise<any>[] = [api.saveSafetySettings(safety)]
    if (snapshot.value) {
      promises.push(api.saveConfig({
        expectedRevision: snapshot.value.revision,
        config: { ...snapshot.value.config, rules: nextRules },
      }))
    }
    await Promise.all(promises)
    saveState.value = 'saved'
    setTimeout(() => { saveState.value = 'idle'; }, 4000)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存配置失败'
    saveState.value = 'error'
  }
}

onMounted(() => {
  void loadAllSettings()
})
</script>

<template>
  <div class="rules-page">
    <div class="rules-container">
      <!-- Header -->
      <header class="rules-header">
        <div class="header-left">
          <h2>{{ t('rules.title') }}</h2>
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
            <span>{{ saveState === 'saving' ? t('rules.btn.saving') : t('rules.btn.save') }}</span>
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
            <strong>{{ t('rules.callout.title') }}</strong>
            <span class="pill-active">已保护</span>
          </div>
          <p>
            {{ t('rules.callout.desc') }}
          </p>
        </div>
      </div>

      <!-- Emergency Mode & Control Switcher -->
      <div class="mode-control-banner">
        <div class="mode-banner-info">
          <div class="mode-badge-line">
            <span class="banner-tag">{{ t('rules.mode.tag') }}</span>
            <h3>{{ t('rules.mode.title') }}</h3>
          </div>
          <p>{{ t('rules.mode.desc') }}</p>
        </div>
        <div class="mode-btn-group">
          <button
            type="button"
            :class="['mode-choice-btn', 'live', { active: controlMode === 'live' }]"
            @click="switchControlMode('live')"
          >
            <span class="mode-dot live"></span>
            <div class="mode-btn-text">
              <strong>{{ t('rules.mode.live') }}</strong>
              <small>{{ t('rules.mode.live.desc') }}</small>
            </div>
          </button>
          <button
            type="button"
            :class="['mode-choice-btn', 'dryrun', { active: controlMode === 'dry_run' }]"
            @click="switchControlMode('dry_run')"
          >
            <span class="mode-dot dryrun"></span>
            <div class="mode-btn-text">
              <strong>{{ t('rules.mode.dryrun') }}</strong>
              <small>{{ t('rules.mode.dryrun.desc') }}</small>
            </div>
          </button>
          <button
            type="button"
            :class="['mode-choice-btn', 'paused', { active: controlMode === 'paused' }]"
            @click="switchControlMode('paused')"
          >
            <span class="mode-dot paused"></span>
            <div class="mode-btn-text">
              <strong>{{ t('rules.mode.paused') }}</strong>
              <small>{{ t('rules.mode.paused.desc') }}</small>
            </div>
          </button>
        </div>
      </div>

      <!-- Main Form Grid -->
      <div class="rules-grid">
        <!-- Card 1: Match Conditions -->
        <section class="apple-card">
          <div class="card-header">
            <div class="card-header-title">
              <span class="card-tag">Match Conditions</span>
              <h2>{{ t('rules.card.match') }}</h2>
              <p>当以下所有条件同时满足（AND 逻辑）时，工单才允许提交给 AI 推理。</p>
            </div>
            <span class="condition-badge">AND 条件</span>
          </div>

          <div class="card-body">
            <!-- SaaS Tag -->
            <div class="form-item">
              <div class="item-label-group">
                <label>{{ t('rules.saas_tag.label') }}</label>
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
                <label>{{ t('rules.assignee.label') }}</label>
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
                <label>{{ t('rules.statuses.label') }}</label>
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
                <label>{{ t('rules.aliases.label') }}</label>
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
                    :placeholder="t('rules.aliases.hint')"
                    @keydown.enter.prevent="addAlias"
                  />
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Card 2: Quarantine & Blacklist Isolation -->
        <section class="apple-card">
          <div class="card-header">
            <div class="card-header-title">
              <span class="card-tag">Risk Quarantine</span>
              <h2>{{ t('rules.card.blacklist') }}</h2>
              <p>{{ t('rules.card.blacklist.desc') }}</p>
            </div>
            <span class="blacklist-badge">{{ safety.ticket_blacklist.length }} 个隔离单</span>
          </div>

          <div class="card-body">
            <div class="form-item">
              <div class="item-label-group">
                <label>{{ t('rules.blacklist.label') }}</label>
                <small>命中此列表的工单在收到 Webhook 时直接跳过并丢弃，绝不上锁 Worker</small>
              </div>
              <div class="blacklist-box">
                <div class="blacklist-chips">
                  <span v-for="tId in safety.ticket_blacklist" :key="tId" class="blacklist-chip">
                    <span class="chip-shield">🛡️</span>
                    <strong>#{{ tId }}</strong>
                    <button type="button" class="tag-remove" @click="removeBlacklistTicket(tId)">×</button>
                  </span>
                  <div class="add-blacklist-inline">
                    <input
                      v-model="blacklistDraft"
                      type="number"
                      class="blacklist-input"
                      placeholder="输入工单号回车添加隔离…"
                      @keydown.enter.prevent="addBlacklistTicket"
                    />
                    <button type="button" class="btn-add-chip" @click="addBlacklistTicket">{{ t('rules.blacklist.add') }}</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- Card 3: Feishu Alert Webhook -->
        <section class="apple-card">
          <div class="card-header">
            <div class="card-header-title">
              <span class="card-tag">Alerts & Escalation</span>
              <h2>{{ t('rules.card.feishu') }}</h2>
              <p>{{ t('rules.card.feishu.desc') }}</p>
            </div>
            <AppIcon name="bell" :size="18" class="header-icon" />
          </div>

          <div class="card-body">
            <div class="form-item">
              <div class="item-label-group">
                <label>{{ t('rules.feishu.url.label') }}</label>
                <small>支持填入飞书自定义机器人 Webhook URL (包含 token)</small>
              </div>
              <div class="feishu-input-group">
                <input
                  v-model="safety.feishu_webhook_url"
                  type="text"
                  class="apple-input"
                  placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                />
                <button
                  type="button"
                  class="btn-feishu-test"
                  :disabled="feishuTesting"
                  @click="testFeishu"
                >
                  <AppIcon name="activity" :size="14" />
                  <span>{{ feishuTesting ? t('rules.feishu.testing') : t('rules.feishu.btn.test') }}</span>
                </button>
              </div>
            </div>

            <!-- Feishu Test Result Card -->
            <div v-if="feishuResult" :class="['feishu-result-card', { success: feishuResult.ok, failed: !feishuResult.ok }]">
              <span class="result-icon">{{ feishuResult.ok ? '✅' : '❌' }}</span>
              <div class="result-text">
                <strong>{{ feishuResult.ok ? '飞书卡片推送成功' : '飞书推送失败' }} (HTTP {{ feishuResult.status_code }}, {{ feishuResult.latency_ms }}ms)</strong>
                <span>{{ feishuResult.message }}</span>
              </div>
            </div>

            <div class="toggle-row">
              <div class="toggle-label-group">
                <label>{{ t('rules.feishu.auto_escalate') }}</label>
                <p>工单若重试耗尽仍然失败，系统自动流转为转人工状态并推送飞书待办给值班同学。</p>
              </div>
              <label class="apple-switch">
                <input v-model="safety.auto_escalate_on_fail" type="checkbox" />
                <span class="slider round" />
              </label>
            </div>
          </div>
        </section>

        <!-- Card 4: Delivery Guardrails -->
        <section class="apple-card">
          <div class="card-header">
            <div class="card-header-title">
              <span class="card-tag">Delivery Guardrails</span>
              <h2>{{ t('rules.card.delivery') }}</h2>
              <p>AI 生成答复后进入交付控制器，进行安全验证与统一前台投递。</p>
            </div>
            <AppIcon name="shield" :size="18" class="header-icon" />
          </div>

          <div class="card-body">
            <!-- Reply Toggle Switch -->
            <div class="toggle-card">
              <div class="toggle-info">
                <strong>{{ t('rules.reply.enable') }}</strong>
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
                <label>{{ t('rules.reply.lang') }}</label>
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
                <label>{{ t('rules.reply.footer') }}</label>
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

        <!-- Card 3: Debounce & Freshness Guard -->
        <section class="apple-card">
          <div class="card-header">
            <div class="card-header-title">
              <span class="card-tag">Concurrency & Freshness Guard</span>
              <h2>防抖窗口与多消息上下文追随</h2>
              <p>避免用户分段发多条消息时触发多次重复推理，并在答复发出前进行新鲜度核验。</p>
            </div>
            <span class="pill-active">已激活</span>
          </div>

          <div class="card-body">
            <!-- Debounce Window -->
            <div class="toggle-row">
              <div class="toggle-label-group">
                <label>10 秒静默防抖窗口 (Debounce Window)</label>
                <p>收到客户消息后静默缓冲 10 秒；若 10 秒内客户继续追加多条短句，系统自动顺延 10 秒倒计时并合并多段提问，待客户发言停顿后统一提交 AI 一次性排障，大幅提升推理连贯性并节省模型 Token。</p>
              </div>
              <span class="tag-status">10 秒缓冲</span>
            </div>

            <!-- Pre-Delivery Freshness Check -->
            <div class="toggle-row">
              <div class="toggle-label-group">
                <label>投递前工单新鲜度核验 (Pre-Delivery Freshness Guard)</label>
                <p>AI 完成推理后、调用 Browser Relay 发出答复前，强制重新拉取工单最新会话流水；若检测到客户在推理期间发送了新消息或要求转人工，立即废弃当前旧答复并自动追随最新上下文，坚决避免跨消息误答。</p>
              </div>
              <span class="tag-status">实时核验</span>
            </div>
          </div>
        </section>
      </div>

      <!-- Card 4: Evaluation Preview Pipeline -->
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
            <small>Webhook 实时接收</small>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-step">
            <span class="step-num">02</span>
            <strong>10s 防抖与多消息合并</strong>
            <small>静默 10s 后出库</small>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-step">
            <span class="step-num">03</span>
            <strong>模式 B 准入保护</strong>
            <small>#{{ rules.saasTag }} · 未分配人工</small>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-step">
            <span class="step-num">04</span>
            <strong>Pi Agent 证据推理</strong>
            <small>OpenConnector 只读调查</small>
          </div>
          <div class="flow-arrow">→</div>
          <div class="flow-step">
            <span class="step-num">05</span>
            <strong>新鲜度核验与投递</strong>
            <small>比对最新游标后发信</small>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.rules-page {
  min-height: 100%;
  background: var(--surface);
  color: var(--ink);
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
  align-items: center;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 20px;
}

.rules-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
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
  color: var(--green);
  background: var(--green-soft);
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
  background: var(--ink);
  color: var(--surface);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: opacity 0.15s;
}
.btn-save:hover { opacity: 0.9; }
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
  background: var(--red-soft);
  border: 1px solid rgba(255, 59, 48, 0.25);
  color: var(--red);
}

/* Mode B Callout */
.protection-callout {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 18px 22px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 24px;
}
.callout-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--blue-soft);
  color: var(--blue);
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
  color: var(--ink);
}
.pill-active {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--green);
  background: var(--green-soft);
  padding: 2px 7px;
  border-radius: 10px;
}
.callout-content p {
  margin: 0;
  font-size: 12px;
  color: var(--ink-soft);
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
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow-sm);
  padding: 24px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
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
  color: var(--muted);
}
.card-header-title h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}
.card-header-title p {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.condition-badge {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--blue);
  background: var(--blue-soft);
  padding: 3px 8px;
  border-radius: 6px;
}
.header-icon {
  color: var(--muted);
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
  color: var(--ink);
  margin-bottom: 2px;
}
.item-label-group small {
  display: block;
  font-size: 11px;
  color: var(--muted);
}

/* Inputs */
.apple-input {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  font-size: 13px;
  color: var(--ink);
  background: var(--surface-soft);
  outline: none;
  transition: border-color 0.15s;
}
.apple-input:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-soft);
}

.input-prefix-box {
  display: flex;
  align-items: center;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface-soft);
  overflow: hidden;
}
.input-prefix-box:focus-within {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-soft);
}
.prefix {
  padding: 0 12px;
  font-size: 13px;
  color: var(--muted);
  font-weight: 600;
  background: var(--surface);
  border-right: 1px solid var(--line);
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
  color: var(--muted);
  background: var(--surface);
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--line);
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
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  cursor: pointer;
  transition: all 0.15s;
}
.status-choice:hover {
  background: var(--surface-hover);
}
.status-choice.active {
  border-color: var(--blue);
  box-shadow: 0 0 0 1px var(--blue), 0 2px 6px var(--blue-soft);
}
.choice-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--line-strong);
}
.status-choice.active .choice-dot {
  background: var(--green);
}
.choice-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  font-family: ui-monospace, monospace;
}
.choice-check {
  margin-left: auto;
  color: var(--blue);
}

/* Tag Chips */
.tag-input-container {
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface-soft);
  padding: 8px 10px;
  transition: border-color 0.15s;
}
.tag-input-container:focus-within {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-soft);
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
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 12px;
  color: var(--ink);
}
.tag-remove {
  background: transparent;
  border: none;
  font-size: 14px;
  line-height: 1;
  color: var(--muted);
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
  color: var(--ink);
  background: transparent;
  padding: 4px 0;
}

/* Toggle Card */
.toggle-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 10px;
}
.toggle-info strong {
  display: block;
  font-size: 13px;
  color: var(--ink);
  margin-bottom: 2px;
}
.toggle-info p {
  margin: 0;
  font-size: 11px;
  color: var(--muted);
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
  background-color: var(--line-strong);
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
input:checked + .slider { background-color: var(--green); }
input:checked + .slider:before { transform: translateX(20px); }

/* Select & Textarea */
.select-wrapper {
  position: relative;
}
.apple-select {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface-soft);
  color: var(--ink);
  font-size: 13px;
  outline: none;
}
.apple-select:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-soft);
}

.apple-textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface-soft);
  color: var(--ink);
  font-size: 12px;
  line-height: 1.5;
  font-family: ui-monospace, "SF Mono", monospace;
  outline: none;
  resize: vertical;
}
.apple-textarea:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px var(--blue-soft);
}

/* Pipeline Flow */
.preview-card {
  margin-top: 4px;
}
.version-label {
  font-size: 11px;
  color: var(--muted);
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
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.flow-step--active {
  background: var(--surface);
  border-color: var(--blue);
  box-shadow: 0 2px 8px var(--blue-soft);
}
.step-num {
  font-size: 10px;
  font-weight: 700;
  color: var(--blue);
}
.flow-step strong {
  font-size: 13px;
  color: var(--ink);
}
.flow-step small {
  font-size: 11px;
  color: var(--muted);
}
.flow-arrow {
  color: var(--muted);
  font-size: 16px;
}

/* Mode Control Banner */
.mode-control-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 24px;
}

.mode-badge-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.banner-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  background: var(--surface-hover);
  color: var(--muted);
  padding: 2px 6px;
  border-radius: 4px;
}

.mode-banner-info h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.mode-banner-info p {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.mode-btn-group {
  display: flex;
  background: var(--canvas);
  padding: 4px;
  border-radius: 10px;
  gap: 4px;
  border: 1px solid var(--line);
}

.mode-choice-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}

.mode-choice-btn:hover {
  background: var(--surface-hover);
}

.mode-choice-btn.active {
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.mode-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.mode-dot.live { background: var(--green); }
.mode-dot.dryrun { background: var(--amber); }
.mode-dot.paused { background: var(--red); }

.mode-btn-text strong {
  display: block;
  font-size: 12px;
  color: var(--ink);
}

.mode-btn-text small {
  display: block;
  font-size: 10px;
  color: var(--muted);
}

/* Blacklist & Quarantine */
.blacklist-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--red);
  background: var(--red-soft);
  padding: 3px 8px;
  border-radius: 6px;
}

.blacklist-box {
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 12px;
}

.blacklist-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.blacklist-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: var(--surface);
  border: 1px solid rgba(255, 59, 48, 0.25);
  color: var(--red);
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
}

.chip-shield {
  font-size: 12px;
}

.add-blacklist-inline {
  display: flex;
  align-items: center;
  gap: 6px;
}

.blacklist-input {
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 12px;
  width: 150px;
  outline: none;
  background: var(--surface);
  color: var(--ink);
}

.blacklist-input:focus {
  border-color: var(--blue);
}

.btn-add-chip {
  background: var(--blue);
  color: #fff;
  border: none;
  font-size: 12px;
  font-weight: 500;
  padding: 5px 10px;
  border-radius: 6px;
  cursor: pointer;
}

.btn-add-chip:hover {
  opacity: 0.9;
}

/* Feishu Webhook */
.feishu-input-group {
  display: flex;
  gap: 8px;
}

.btn-feishu-test {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  background: var(--surface-soft);
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ink);
  cursor: pointer;
  transition: all 0.15s;
}

.btn-feishu-test:hover:not(:disabled) {
  background: var(--surface-hover);
}

.btn-feishu-test:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.feishu-result-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 12px;
}

.feishu-result-card.success {
  background: var(--green-soft);
  border: 1px solid rgba(52, 199, 89, 0.3);
  color: var(--green);
}

.feishu-result-card.failed {
  background: var(--red-soft);
  border: 1px solid rgba(255, 59, 48, 0.3);
  color: var(--red);
}

.result-text strong {
  display: block;
}

.result-text span {
  font-size: 11px;
}
</style>
