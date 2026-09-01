<script setup lang="ts">
import { inject, onMounted, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import type { AdminConfig, HarnessConfig, SandboxPolicy } from '../types'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

const config = reactive<HarnessConfig>({
  rootModel: 'unconfigured',
  rootProvider: 'unconfigured',
  subagents: [],
  contextWindow: '128k tokens',
  softCompaction: 60,
  hardCheckpoint: 80,
  sandboxCpu: '0.5 CPU',
  sandboxMemory: '1 GiB',
  timeoutMinutes: 30,
  maxRetries: 1,
  maxToolRounds: 24,
})
const policy = ref<SandboxPolicy | null>(null)
const revision = ref('—')
const snapshot = ref<Awaited<ReturnType<typeof api.getConfig>> | null>(null)
const saveState = ref<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('loading')
const errorMessage = ref('')

function displayProvider(provider: string): string {
  return provider === 'openai' ? 'OpenAI' : provider
}

function applySnapshot(next: Awaited<ReturnType<typeof api.getConfig>>, nextPolicy: SandboxPolicy) {
  snapshot.value = next
  revision.value = next.revision
  config.rootModel = next.config.rootModel.model
  config.rootProvider = displayProvider(next.config.rootModel.provider)
  config.subagents = next.config.subagentSlots.map((slot) => ({
    id: slot.slotId,
    name: slot.slotId,
    purpose: '独立调查任务',
    model: slot.model,
    provider: slot.provider,
    credentialRef: slot.credentialRef,
    contextWindowTokens: slot.contextWindowTokens,
    maxToolRounds: slot.maxToolRounds,
    maxParallel: slot.maxParallel,
    enabled: slot.enabled,
  }))
  config.contextWindow = `${Math.round(next.config.contextWindowTokens / 1000)}k tokens`
  config.softCompaction = next.config.softCompactionPercent
  config.hardCheckpoint = next.config.hardCheckpointPercent
  config.sandboxCpu = `${nextPolicy.attempt1.cpu} CPU`
  config.sandboxMemory = `${nextPolicy.attempt1.memoryMib / 1024} GiB`
  config.timeoutMinutes = Math.round(nextPolicy.attempt1.timeoutSeconds / 60)
  config.maxRetries = nextPolicy.attempt2.cpu > nextPolicy.attempt1.cpu ? 1 : 0
  config.maxToolRounds = next.config.maxToolRounds
}

async function loadConfig(preserveError = false) {
  saveState.value = 'loading'
  if (!preserveError) errorMessage.value = ''
  try {
    const [next, nextPolicy] = await Promise.all([api.getConfig(), api.getSandboxPolicy()])
    policy.value = nextPolicy
    applySnapshot(next, nextPolicy)
    saveState.value = 'idle'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取 Harness 配置失败'
    saveState.value = 'error'
  }
}

function isConflict(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error && (error as { status?: unknown }).status === 409
}

function contextTokens(value: string): number {
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed * 1000 : 128_000
}

function configForSave(current: AdminConfig): AdminConfig {
  return {
    ...current,
    rootModel: { ...current.rootModel, model: config.rootModel },
    subagentSlots: config.subagents.map((slot) => ({
      slotId: slot.id,
      enabled: slot.enabled,
      provider: slot.provider,
      model: slot.model,
      credentialRef: slot.credentialRef,
      contextWindowTokens: slot.contextWindowTokens,
      maxToolRounds: slot.maxToolRounds,
      maxParallel: slot.maxParallel,
    })),
    contextWindowTokens: contextTokens(config.contextWindow),
    softCompactionPercent: config.softCompaction,
    hardCheckpointPercent: config.hardCheckpoint,
    maxToolRounds: config.maxToolRounds,
  }
}

function addSubagent() {
  let nextNumber = config.subagents.length + 1
  while (config.subagents.some((slot) => slot.id === `subagent-${nextNumber}`)) {
    nextNumber += 1
  }
  const slotId = `subagent-${nextNumber}`
  config.subagents.push({
    id: slotId,
    name: slotId,
    purpose: '独立调查任务',
    model: 'gpt-5.6-luna',
    provider: 'openai',
    credentialRef: `secret/${slotId}`,
    contextWindowTokens: 32_000,
    maxToolRounds: 12,
    maxParallel: 1,
    enabled: true,
  })
}

function removeSubagent(index: number) {
  config.subagents.splice(index, 1)
}

async function saveConfig() {
  if (!snapshot.value) {
    await loadConfig()
    return
  }
  saveState.value = 'saving'
  errorMessage.value = ''
  try {
    const saved = await api.saveConfig({ expectedRevision: snapshot.value.revision, config: configForSave(snapshot.value.config) })
    snapshot.value = saved
    revision.value = saved.revision
    saveState.value = 'saved'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存 Harness 配置失败'
    saveState.value = 'error'
    if (isConflict(error)) {
      await loadConfig(true)
      errorMessage.value = '配置版本已过期，已重新加载最新配置；请确认后再次保存。'
      saveState.value = 'error'
    }
  }
}

onMounted(() => { void loadConfig() })
</script>

<template>
  <div class="page-view">
    <div class="page-heading">
      <div>
        <p class="eyebrow">HARNESS / CONFIGURATION</p>
        <h1>Harness 配置</h1>
        <p class="page-description">管理 Root Agent、独立 Subagent 和每次运行的资源边界。</p>
      </div>
      <div class="heading-actions">
        <span v-if="saveState === 'saved'" class="save-confirm"><AppIcon name="check" :size="14" />已保存到修订 {{ revision }}</span>
        <button type="button" class="button button--primary" data-testid="save-config" :disabled="saveState === 'saving' || saveState === 'loading'" @click="saveConfig">
          <AppIcon name="check" :size="15" />{{ saveState === 'saving' ? '保存中…' : '保存配置' }}
        </button>
      </div>
    </div>

    <div v-if="errorMessage" class="notice-banner notice-banner--danger" role="alert"><AppIcon name="alert" :size="18" /><div><strong>Harness 配置不可用</strong><span>{{ errorMessage }}</span></div></div>

    <div class="notice-banner notice-banner--info">
      <AppIcon name="shield" :size="18" />
      <div><strong>配置修订 {{ revision }} 正在使用</strong><span>每个新运行都会固定到创建时的不可变配置。修改只会影响后续工单。</span></div>
      <span class="notice-time">{{ snapshot ? `最后更新 · ${new Date(snapshot.createdAt).toLocaleString()}` : '正在读取…' }}</span>
    </div>

    <section class="panel config-section">
      <div class="panel-heading panel-heading--with-description">
        <div><p class="eyebrow">MODEL ROUTING</p><h2>模型配置</h2><p>Root Agent 和 Subagent 使用独立模型设置，凭据不会互相继承。</p></div>
        <div class="heading-actions"><StatusBadge :label="`${config.subagents.length + 1} 个已配置`" tone="success" /><button type="button" class="button button--secondary" data-testid="add-subagent" :disabled="saveState === 'loading' || saveState === 'saving'" @click="addSubagent"><AppIcon name="spark" :size="15" />新增 Subagent</button></div>
      </div>
      <div class="model-grid">
        <article class="model-card model-card--root" data-testid="root-model">
          <div class="model-card__heading"><span class="model-avatar model-avatar--root"><AppIcon name="spark" :size="18" /></span><div><span class="card-kicker">ROOT AGENT</span><h3>Root Agent</h3></div><StatusBadge label="默认" tone="info" /></div>
          <p class="model-purpose">负责完整工单调查、Todo 编排与最终结构化结果。</p>
          <label class="field-label" for="root-model">模型</label>
          <select id="root-model" v-model="config.rootModel" class="field-control"><option>gpt-5.6-sol</option><option>gpt-5.6-terra</option><option>gpt-5.6-luna</option></select>
          <div class="model-meta"><span>Provider</span><strong>{{ config.rootProvider }}</strong></div>
          <div class="model-meta"><span>最大上下文</span><strong>{{ config.contextWindow }}</strong></div>
        </article>
        <article v-for="(agent, index) in config.subagents" :key="index" class="model-card" :data-testid="`subagent-card-${index}`">
          <div class="model-card__heading"><span class="model-avatar"><AppIcon name="box" :size="18" /></span><div><span class="card-kicker">SUBAGENT</span><h3>{{ agent.id || '未命名 Subagent' }}</h3></div><label class="switch switch--small"><input v-model="agent.enabled" type="checkbox" :aria-label="`启用 ${agent.id || 'Subagent'}`"><span /></label></div>
          <div class="model-card__actions"><button type="button" class="text-button text-button--compact" :data-testid="`remove-slot-${index}`" @click="removeSubagent(index)">删除 slot</button></div>
          <label class="field-label" :for="`slot-id-${index}`">Slot ID</label>
          <input :id="`slot-id-${index}`" v-model="agent.id" class="field-control" :data-testid="`slot-id-${index}`" type="text" autocomplete="off">
          <label class="field-label" :for="`slot-provider-${index}`">Provider</label>
          <input :id="`slot-provider-${index}`" v-model="agent.provider" class="field-control" :data-testid="`slot-provider-${index}`" type="text" autocomplete="off">
          <label class="field-label" :for="`slot-model-${index}`">模型</label>
          <input :id="`slot-model-${index}`" v-model="agent.model" class="field-control" :data-testid="`slot-model-${index}`" type="text" autocomplete="off">
          <label class="field-label" :for="`slot-credential-ref-${index}`">Credential ref</label>
          <input :id="`slot-credential-ref-${index}`" v-model="agent.credentialRef" class="field-control" :data-testid="`slot-credential-ref-${index}`" type="text" autocomplete="off" placeholder="secret/subagent-id">
          <small class="field-help">仅保存 secret reference，不填写或显示明文凭据。</small>
          <div class="subagent-limits">
            <label class="field-label" :for="`slot-context-window-${index}`">Context window</label>
            <input :id="`slot-context-window-${index}`" v-model.number="agent.contextWindowTokens" class="field-control field-control--number" :data-testid="`slot-context-window-${index}`" type="number" min="16000" max="1000000">
            <label class="field-label" :for="`slot-max-tool-rounds-${index}`">Max Tool rounds</label>
            <input :id="`slot-max-tool-rounds-${index}`" v-model.number="agent.maxToolRounds" class="field-control field-control--number" :data-testid="`slot-max-tool-rounds-${index}`" type="number" min="1" max="100">
            <label class="field-label" :for="`slot-max-parallel-${index}`">Max parallel</label>
            <input :id="`slot-max-parallel-${index}`" v-model.number="agent.maxParallel" class="field-control field-control--number" :data-testid="`slot-max-parallel-${index}`" type="number" min="1" max="8">
          </div>
          <div class="model-meta"><span>状态</span><StatusBadge :label="agent.enabled ? '已启用' : '已停用'" :tone="agent.enabled ? 'success' : 'neutral'" /></div>
        </article>
      </div>
    </section>

    <div class="content-grid content-grid--config">
      <section class="panel settings-panel">
      <div class="panel-heading panel-heading--with-description"><div><p class="eyebrow">CONTEXT GOVERNANCE</p><h2>上下文治理</h2><p>控制模型上下文增长和检查点保留策略。</p></div><AppIcon name="sliders" class="heading-icon" :size="19" /></div>
        <div class="settings-list">
          <label class="setting-row"><span><strong>上下文窗口</strong><small>所有 Root Agent 运行的上限</small></span><select v-model="config.contextWindow" class="field-control field-control--compact"><option>128k tokens</option><option>64k tokens</option><option>256k tokens</option></select></label>
          <label class="setting-row"><span><strong>软压缩阈值</strong><small>达到后保留最近四轮 Tool 输出</small></span><div class="input-with-suffix"><input v-model.number="config.softCompaction" class="field-control field-control--number" type="number" min="20" max="90"><span>%</span></div></label>
          <label class="setting-row"><span><strong>硬检查点阈值</strong><small>达到后重建上下文并恢复活动 Todo</small></span><div class="input-with-suffix"><input v-model.number="config.hardCheckpoint" class="field-control field-control--number" type="number" min="40" max="95"><span>%</span></div></label>
        </div>
      </section>
      <section class="panel settings-panel">
        <div class="panel-heading panel-heading--with-description"><div><p class="eyebrow">SANDBOX POLICY</p><h2>沙箱策略</h2><p>每次运行创建临时隔离环境，终态后自动销毁。</p></div><AppIcon name="box" class="heading-icon" :size="19" /></div>
        <div class="settings-list">
          <div class="setting-row"><span><strong>默认 CPU</strong><small>资源不足时允许一次重试</small></span><span class="read-only-value"><AppIcon name="shield" :size="12" />{{ config.sandboxCpu }}</span></div>
          <div class="setting-row"><span><strong>内存上限</strong><small>单个处理运行的最大内存</small></span><span class="read-only-value"><AppIcon name="shield" :size="12" />{{ config.sandboxMemory }}</span></div>
          <div class="setting-row"><span><strong>超时时间</strong><small>超过后停止 Tool Loop</small></span><span class="read-only-value"><AppIcon name="shield" :size="12" />{{ config.timeoutMinutes }} 分钟</span></div>
          <div class="setting-row"><span><strong>资源重试次数</strong><small>仅用于 OOM / CPU exhaustion</small></span><span class="read-only-value"><AppIcon name="shield" :size="12" />{{ config.maxRetries }} 次 · 固定上限</span></div>
        </div>
      </section>
    </div>

    <section class="panel loop-panel">
      <div class="panel-heading panel-heading--with-description"><div><p class="eyebrow">TODO / TOOL LOOP</p><h2>执行边界</h2><p>当仍有可执行 Todo 时，Harness 会继续 Tool Loop。</p></div><div class="loop-limit"><span>最大 Tool rounds</span><input v-model.number="config.maxToolRounds" class="field-control field-control--number" type="number" min="1" max="100"></div></div>
      <div class="flow-steps"><div class="flow-step flow-step--active"><span>01</span><strong>Fetch ticket</strong><small>读取最新快照</small></div><div class="flow-line" /><div class="flow-step"><span>02</span><strong>Build Todo</strong><small>生成调查计划</small></div><div class="flow-line" /><div class="flow-step"><span>03</span><strong>Tool Loop</strong><small>执行并记录证据</small></div><div class="flow-line" /><div class="flow-step"><span>04</span><strong>Finalize</strong><small>刷新并交付</small></div></div>
    </section>
  </div>
</template>
