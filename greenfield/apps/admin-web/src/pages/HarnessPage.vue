<script setup lang="ts">
import { reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { harnessConfig } from '../data/mockData'

const config = reactive({
  ...harnessConfig,
  subagents: harnessConfig.subagents.map((agent) => ({ ...agent })),
})
const saveState = ref<'idle' | 'saving' | 'saved'>('idle')

function saveConfig() {
  saveState.value = 'saving'
  window.setTimeout(() => { saveState.value = 'saved' }, 450)
}
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
        <span v-if="saveState === 'saved'" class="save-confirm"><AppIcon name="check" :size="14" />已保存到修订 r43</span>
        <button type="button" class="button button--primary" :disabled="saveState === 'saving'" @click="saveConfig">
          <AppIcon name="check" :size="15" />{{ saveState === 'saving' ? '保存中…' : '保存配置' }}
        </button>
      </div>
    </div>

    <div class="notice-banner notice-banner--info">
      <AppIcon name="shield" :size="18" />
      <div><strong>配置修订 r42 正在使用</strong><span>每个新运行都会固定到创建时的不可变配置。修改只会影响后续工单。</span></div>
      <span class="notice-time">最后更新 · 今天 09:20</span>
    </div>

    <section class="panel config-section">
      <div class="panel-heading panel-heading--with-description">
        <div><p class="eyebrow">MODEL ROUTING</p><h2>模型配置</h2><p>Root Agent 和 Subagent 使用独立模型设置，凭据不会互相继承。</p></div>
        <StatusBadge label="3 个已配置" tone="success" />
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
        <article v-for="agent in config.subagents" :key="agent.id" class="model-card">
          <div class="model-card__heading"><span class="model-avatar"><AppIcon name="box" :size="18" /></span><div><span class="card-kicker">SUBAGENT</span><h3>{{ agent.name }}</h3></div><label class="switch switch--small"><input v-model="agent.enabled" type="checkbox" :aria-label="`启用 ${agent.name}`"><span /></label></div>
          <p class="model-purpose">{{ agent.purpose }}</p>
          <label class="field-label" :for="`model-${agent.id}`">模型</label>
          <select :id="`model-${agent.id}`" v-model="agent.model" class="field-control"><option>gpt-5.6-terra</option><option>gpt-5.6-luna</option><option>gpt-5.6-sol</option></select>
          <div class="model-meta"><span>Provider</span><strong>{{ agent.provider }}</strong></div>
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
