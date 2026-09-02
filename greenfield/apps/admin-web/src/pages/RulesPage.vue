<script setup lang="ts">
import { inject, onMounted, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import type { ProcessingRules } from '../types'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

const rules = reactive<ProcessingRules>({
  saasTag: 'saas',
  assignee: 'saas-support',
  statuses: ['new', 'open'],
  humanAliases: ['human'],
  replyEnabled: false,
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
    statuses: [...next.config.rules.statuses],
    humanAliases: [...next.config.rules.humanAliases],
    footers: { ...next.config.rules.footers },
  })
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
  if (alias && !rules.humanAliases.includes(alias)) rules.humanAliases.push(alias)
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
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存处理规则失败'
    saveState.value = 'error'
    if (isConflict(error)) {
      await loadRules(true)
      errorMessage.value = '配置版本已过期，已重新加载最新规则；请确认后再次保存。'
      saveState.value = 'error'
    }
  }
}

onMounted(() => { void loadRules() })
</script>

<template>
  <div class="page-view">
    <div class="page-heading">
      <div>
        <p class="eyebrow">INGRESS / DETERMINISTIC FILTER</p>
        <h1>处理规则</h1>
        <p class="page-description">配置哪些 Zendesk 事件可以进入 Harness，以及如何生成本地化回复。</p>
      </div>
      <div class="heading-actions"><span v-if="saveState === 'saved'" class="save-confirm"><AppIcon name="check" :size="14" />已保存到修订 {{ revision }}</span><button type="button" class="button button--primary" :disabled="saveState === 'saving' || saveState === 'loading'" @click="saveRules"><AppIcon name="check" :size="15" />{{ saveState === 'saving' ? '保存中…' : '保存规则' }}</button></div>
    </div>

    <div v-if="errorMessage" class="notice-banner notice-banner--danger" role="alert"><AppIcon name="alert" :size="18" /><div><strong>处理规则不可用</strong><span>{{ errorMessage }}</span></div></div>

    <div class="notice-banner notice-banner--warning"><AppIcon name="alert" :size="18" /><div><strong>human 是确定性的入口行为</strong><span>命中任一 alias 会取消活动运行并阻止草稿交付，不会交给模型自行判断。</span></div><StatusBadge label="Deterministic" tone="warning" /></div>

    <div class="content-grid content-grid--rules">
      <section class="panel rules-main-panel">
        <div class="panel-heading panel-heading--with-description"><div><p class="eyebrow">MATCH CONDITIONS</p><h2>Webhook 标签</h2><p>以下条件全部满足时，只提交 ticket_id 到 durable queue。</p></div><StatusBadge label="AND 条件" tone="info" /></div>
        <div class="rule-form">
          <label class="form-field"><span class="field-label">SaaS 标签</span><span class="field-hint">用于识别需要 SaaS 支持的工单</span><div class="input-with-prefix"><span>#</span><input v-model="rules.saasTag" class="field-control" type="text" aria-label="SaaS 标签"></div></label>
        <label class="form-field"><span class="field-label">SaaS 受托组 ID</span><span class="field-hint">仅处理分配给指定 Zendesk 组的工单</span><input v-model="rules.assignee" class="field-control" type="text" aria-label="SaaS 受托组 ID"></label>
          <div class="form-field"><span class="field-label">允许的工单状态</span><span class="field-hint">事件状态必须处于下列范围</span><div class="choice-grid"><label v-for="status in statuses" :key="status" class="choice-card" :class="{ 'choice-card--selected': rules.statuses.includes(status) }"><input type="checkbox" :checked="rules.statuses.includes(status)" @change="toggleStatus(status)"><span class="choice-check"><AppIcon name="check" :size="12" /></span><strong>{{ status }}</strong></label></div></div>
          <div class="form-field"><span class="field-label">Human aliases</span><span class="field-hint">消息中命中这些关键词时，立即转人工</span><div class="token-input"><span v-for="alias in rules.humanAliases" :key="alias" class="token">{{ alias }}<button type="button" :aria-label="`移除 ${alias}`" @click="removeAlias(alias)">×</button></span><input v-model="aliasDraft" type="text" placeholder="添加关键词…" aria-label="添加 human alias" @keydown.enter.prevent="addAlias"></div></div>
        </div>
      </section>

      <section class="panel rules-side-panel">
        <div class="panel-heading panel-heading--with-description"><div><p class="eyebrow">DELIVERY GUARDRAILS</p><h2>回复策略</h2><p>模型只生成草稿，交付由控制器负责。</p></div><AppIcon name="shield" class="heading-icon" :size="19" /></div>
        <div class="toggle-setting"><div><strong>启用客户回复</strong><small>完成门控通过后允许进入 outbox</small></div><label class="switch"><input v-model="rules.replyEnabled" type="checkbox" aria-label="启用客户回复"><span /></label></div>
        <div class="divider" />
        <label class="form-field"><span class="field-label">默认回复语言</span><span class="field-hint">按 ticket 语言覆盖前的默认值</span><select v-model="rules.footerLocale" class="field-control"><option value="zh-CN">简体中文 · zh-CN</option><option value="en">English · en</option><option value="en-US">English · en-US</option><option value="ja">日本語 · ja</option><option value="ja-JP">日本語 · ja-JP</option></select></label>
        <div class="footer-editor"><div class="footer-editor__heading"><span class="field-label">本地化 footer</span><StatusBadge label="追加一次" tone="success" /></div><textarea v-model="rules.footers[rules.footerLocale]" class="field-control footer-textarea" :aria-label="`${rules.footerLocale} footer`" /><p>Footer 由 delivery controller 追加，模型无法修改或重复插入。</p></div>
      </section>
    </div>

    <section class="panel rule-preview-panel">
      <div class="panel-heading"><div><p class="eyebrow">EVALUATION PREVIEW</p><h2>规则预览</h2></div><span class="preview-revision">当前草稿 · 未发布</span></div>
      <div class="preview-flow"><div class="preview-node"><span class="preview-node__index">01</span><strong>Zendesk event</strong><small>ticket_id + event metadata</small></div><AppIcon name="chevron" class="preview-arrow" :size="18" /><div class="preview-node"><span class="preview-node__index">02</span><strong>全部命中</strong><small>{{ rules.saasTag }} · {{ rules.assignee }} · {{ rules.statuses.join(' / ') }}</small></div><AppIcon name="chevron" class="preview-arrow" :size="18" /><div class="preview-node preview-node--success"><span class="preview-node__index"><AppIcon name="check" :size="13" /></span><strong>提交 job</strong><small>只提交 ticket_id</small></div></div>
    </section>
  </div>
</template>
