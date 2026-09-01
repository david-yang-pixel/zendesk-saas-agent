<script setup lang="ts">
import { inject, onMounted, reactive, ref } from 'vue'
import { ADMIN_API_KEY } from '../api/client'
import type { AdminApiClient } from '../api/client'
import AppIcon from './AppIcon.vue'
import StatusBadge from './StatusBadge.vue'
import type { ModelCredentialStatus } from '../types'

const injectedApi = inject(ADMIN_API_KEY)
if (!injectedApi) throw new Error('Admin API client is not provided')
const api: AdminApiClient = injectedApi

const credentials = ref<ModelCredentialStatus[]>([])
const secrets = reactive<Record<string, string>>({})
const busyRef = ref<string | null>(null)
const errorMessage = ref('')
const successMessage = ref('')
const loading = ref(true)

async function loadCredentials() {
  loading.value = true
  errorMessage.value = ''
  try {
    credentials.value = await api.getModelCredentials()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取模型凭据失败'
  } finally {
    loading.value = false
  }
}

async function replaceCredential(item: ModelCredentialStatus) {
  const secret = secrets[item.credentialRef] ?? ''
  if (secret.length < 8) {
    errorMessage.value = '请输入完整的新密钥。'
    return
  }
  busyRef.value = item.credentialRef
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await api.replaceModelCredential({ credentialRef: item.credentialRef, secret })
    const index = credentials.value.findIndex((entry) => entry.credentialRef === item.credentialRef)
    if (index >= 0) credentials.value[index] = result.credential
    secrets[item.credentialRef] = ''
    successMessage.value = `${item.credentialRef} 已验证并更换，后续新沙箱立即使用。`
  } catch (error) {
    secrets[item.credentialRef] = ''
    errorMessage.value = error instanceof Error ? error.message : '验证或更换模型凭据失败'
  } finally {
    busyRef.value = null
  }
}

onMounted(() => { void loadCredentials() })
</script>

<template>
  <section class="panel credential-panel" data-testid="model-credential-panel">
    <div class="panel-heading panel-heading--with-description">
      <div>
        <p class="eyebrow">MODEL CREDENTIALS</p>
        <h2>模型凭据</h2>
        <p>候选密钥会先调用当前模型验证；失败不会覆盖现有值，明文也不会再次显示。</p>
      </div>
      <button type="button" class="button button--secondary" :disabled="loading || busyRef !== null" @click="loadCredentials">
        <AppIcon name="refresh" :size="15" />刷新状态
      </button>
    </div>

    <div v-if="errorMessage" class="credential-message credential-message--danger" role="alert">{{ errorMessage }}</div>
    <div v-if="successMessage" class="credential-message credential-message--success" role="status">{{ successMessage }}</div>
    <div v-if="loading" class="credential-empty">正在读取脱敏凭据状态…</div>
    <div v-else-if="credentials.length === 0" class="credential-empty">当前模型配置没有 Credential ref。</div>
    <div v-else class="credential-list">
      <article v-for="item in credentials" :key="item.credentialRef" class="credential-row" :data-testid="`credential-${item.credentialRef}`">
        <div class="credential-identity">
          <div class="credential-title">
            <code>{{ item.credentialRef }}</code>
            <StatusBadge :label="item.configured ? '已配置' : '未配置'" :tone="item.configured ? 'success' : 'warning'" />
          </div>
          <span>{{ item.maskedValue ?? '尚无可用凭据' }}</span>
          <small>{{ item.bindings.map((binding) => `${binding.provider} · ${binding.model}`).join(' / ') }}</small>
        </div>
        <div class="credential-replace">
          <label :for="`credential-secret-${item.credentialRef}`">新密钥</label>
          <div class="credential-input-row">
            <input
              :id="`credential-secret-${item.credentialRef}`"
              v-model="secrets[item.credentialRef]"
              class="field-control"
              type="password"
              autocomplete="new-password"
              placeholder="输入完整新密钥"
              :disabled="busyRef !== null"
              :data-testid="`credential-input-${item.credentialRef}`"
            >
            <button
              type="button"
              class="button button--primary"
              :disabled="busyRef !== null || !(secrets[item.credentialRef]?.length >= 8)"
              :data-testid="`credential-replace-${item.credentialRef}`"
              @click="replaceCredential(item)"
            >
              {{ busyRef === item.credentialRef ? '验证中…' : '验证并更换' }}
            </button>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.credential-panel { margin-top: 18px; }
.credential-list { display: grid; gap: 10px; }
.credential-row { display: grid; grid-template-columns: minmax(220px, .8fr) minmax(320px, 1.2fr); gap: 20px; align-items: end; padding: 14px; border: 1px solid #e2e7ee; border-radius: 7px; background: #fbfcfe; }
.credential-identity { display: grid; gap: 5px; min-width: 0; }
.credential-title { display: flex; align-items: center; gap: 8px; }
.credential-title code { overflow: hidden; color: #53647c; font: 500 10px 'DM Mono', monospace; text-overflow: ellipsis; }
.credential-identity > span { color: #6e7e93; font: 500 10px 'DM Mono', monospace; }
.credential-identity small { overflow: hidden; color: #98a4b3; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.credential-replace { display: grid; gap: 6px; }
.credential-replace > label { color: #8190a3; font-size: 9px; font-weight: 600; }
.credential-input-row { display: grid; grid-template-columns: minmax(160px, 1fr) auto; gap: 8px; }
.credential-input-row .button { white-space: nowrap; }
.credential-message, .credential-empty { margin-bottom: 10px; padding: 9px 11px; border-radius: 5px; font-size: 10px; }
.credential-message--danger { color: #a94d59; background: #fff4f5; }
.credential-message--success { color: #3d8062; background: #f0faf5; }
.credential-empty { color: #8c99aa; background: #f7f9fc; }
@media (max-width: 760px) {
  .credential-row { grid-template-columns: 1fr; align-items: stretch; }
  .credential-input-row { grid-template-columns: 1fr; }
}
</style>
