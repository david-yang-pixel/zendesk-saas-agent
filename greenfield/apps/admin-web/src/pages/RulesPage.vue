<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { processingRules } from '../data/mockData'

const rules = reactive({
  ...processingRules,
  statuses: [...processingRules.statuses],
  humanAliases: [...processingRules.humanAliases],
  footers: { ...processingRules.footers },
})
const aliasDraft = ref('')
const saved = ref(false)
const statuses = ['new', 'open', 'pending', 'solved']
const activeFooter = computed(() => rules.footers[rules.footerLocale] ?? '')

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

function saveRules() {
  saved.value = true
  window.setTimeout(() => { saved.value = false }, 1800)
}
</script>

<template>
  <div class="page-view">
    <div class="page-heading">
      <div>
        <p class="eyebrow">INGRESS / DETERMINISTIC FILTER</p>
        <h1>处理规则</h1>
        <p class="page-description">配置哪些 Zendesk 事件可以进入 Harness，以及如何生成本地化回复。</p>
      </div>
      <div class="heading-actions"><span v-if="saved" class="save-confirm"><AppIcon name="check" :size="14" />规则已保存</span><button type="button" class="button button--primary" @click="saveRules"><AppIcon name="check" :size="15" />保存规则</button></div>
    </div>

    <div class="notice-banner notice-banner--warning"><AppIcon name="alert" :size="18" /><div><strong>human 是确定性的入口行为</strong><span>命中任一 alias 会取消活动运行并阻止草稿交付，不会交给模型自行判断。</span></div><StatusBadge label="Deterministic" tone="warning" /></div>

    <div class="content-grid content-grid--rules">
      <section class="panel rules-main-panel">
        <div class="panel-heading panel-heading--with-description"><div><p class="eyebrow">MATCH CONDITIONS</p><h2>Webhook 标签</h2><p>以下条件全部满足时，只提交 ticket_id 到 durable queue。</p></div><StatusBadge label="AND 条件" tone="info" /></div>
        <div class="rule-form">
          <label class="form-field"><span class="field-label">SaaS 标签</span><span class="field-hint">用于识别需要 SaaS 支持的工单</span><div class="input-with-prefix"><span>#</span><input v-model="rules.saasTag" class="field-control" type="text" aria-label="SaaS 标签"></div></label>
          <label class="form-field"><span class="field-label">SaaS assignee</span><span class="field-hint">仅处理分配给指定 Agent 的工单</span><select v-model="rules.assignee" class="field-control"><option>Dify Support Agent</option><option>Dify Support Team</option><option>未分配</option></select></label>
          <div class="form-field"><span class="field-label">允许的工单状态</span><span class="field-hint">事件状态必须处于下列范围</span><div class="choice-grid"><label v-for="status in statuses" :key="status" class="choice-card" :class="{ 'choice-card--selected': rules.statuses.includes(status) }"><input type="checkbox" :checked="rules.statuses.includes(status)" @change="toggleStatus(status)"><span class="choice-check"><AppIcon name="check" :size="12" /></span><strong>{{ status }}</strong></label></div></div>
          <div class="form-field"><span class="field-label">Human aliases</span><span class="field-hint">消息中命中这些关键词时，立即转人工</span><div class="token-input"><span v-for="alias in rules.humanAliases" :key="alias" class="token">{{ alias }}<button type="button" :aria-label="`移除 ${alias}`" @click="removeAlias(alias)">×</button></span><input v-model="aliasDraft" type="text" placeholder="添加关键词…" aria-label="添加 human alias" @keydown.enter.prevent="addAlias"></div></div>
        </div>
      </section>

      <section class="panel rules-side-panel">
        <div class="panel-heading panel-heading--with-description"><div><p class="eyebrow">DELIVERY GUARDRAILS</p><h2>回复策略</h2><p>模型只生成草稿，交付由控制器负责。</p></div><AppIcon name="shield" class="heading-icon" :size="19" /></div>
        <div class="toggle-setting"><div><strong>启用客户回复</strong><small>完成门控通过后允许进入 outbox</small></div><label class="switch"><input v-model="rules.replyEnabled" type="checkbox" aria-label="启用客户回复"><span /></label></div>
        <div class="divider" />
        <label class="form-field"><span class="field-label">默认回复语言</span><span class="field-hint">按 ticket 语言覆盖前的默认值</span><select v-model="rules.footerLocale" class="field-control"><option value="zh-CN">简体中文 · zh-CN</option><option value="en-US">English · en-US</option><option value="ja-JP">日本語 · ja-JP</option></select></label>
        <div class="footer-editor"><div class="footer-editor__heading"><span class="field-label">本地化 footer</span><StatusBadge label="追加一次" tone="success" /></div><textarea v-model="rules.footers[rules.footerLocale]" class="field-control footer-textarea" :aria-label="`${rules.footerLocale} footer`" /><p>Footer 由 delivery controller 追加，模型无法修改或重复插入。</p></div>
      </section>
    </div>

    <section class="panel rule-preview-panel">
      <div class="panel-heading"><div><p class="eyebrow">EVALUATION PREVIEW</p><h2>规则预览</h2></div><span class="preview-revision">当前草稿 · 未发布</span></div>
      <div class="preview-flow"><div class="preview-node"><span class="preview-node__index">01</span><strong>Zendesk event</strong><small>ticket_id + event metadata</small></div><AppIcon name="chevron" class="preview-arrow" :size="18" /><div class="preview-node"><span class="preview-node__index">02</span><strong>全部命中</strong><small>{{ rules.saasTag }} · {{ rules.assignee }} · {{ rules.statuses.join(' / ') }}</small></div><AppIcon name="chevron" class="preview-arrow" :size="18" /><div class="preview-node preview-node--success"><span class="preview-node__index"><AppIcon name="check" :size="13" /></span><strong>提交 job</strong><small>{ ticket_id: 4186 }</small></div></div>
    </section>
  </div>
</template>
