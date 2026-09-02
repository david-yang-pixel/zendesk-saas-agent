<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AdminApiClient, AdminSession } from '../api/client'

const props = defineProps<{
  api: AdminApiClient
  session?: AdminSession | null
  notice?: string
}>()

const emit = defineEmits<{
  authenticated: [session: AdminSession]
  passwordChanged: []
}>()

const username = ref('admin')
const password = ref('')
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const busy = ref(false)
const errorMessage = ref('')
const changingPassword = computed(() => Boolean(props.session?.forcePasswordChange))

async function submitLogin() {
  busy.value = true
  errorMessage.value = ''
  try {
    const session = await props.api.login({ username: username.value, password: password.value })
    password.value = ''
    emit('authenticated', session)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登录失败，请稍后重试。'
    password.value = ''
  } finally {
    busy.value = false
  }
}

async function submitPasswordChange() {
  errorMessage.value = ''
  if (newPassword.value !== confirmPassword.value) {
    errorMessage.value = '两次输入的新密码不一致。'
    return
  }
  busy.value = true
  try {
    await props.api.changePassword({ currentPassword: currentPassword.value, newPassword: newPassword.value })
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    emit('passwordChanged')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '密码修改失败，请稍后重试。'
    currentPassword.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="access-page">
    <section class="access-card" aria-labelledby="access-title">
      <div class="access-brand">
        <div class="brand-mark" aria-hidden="true"><span>C</span></div>
        <div><strong>Dify Casework</strong><small>Agent Harness</small></div>
      </div>

      <template v-if="changingPassword">
        <p class="eyebrow">SECURITY / PASSWORD UPDATE</p>
        <h1 id="access-title">首次登录，请更新密码</h1>
        <p class="access-description">新密码至少 12 位。修改完成后需要重新登录。</p>
        <form class="access-form" @submit.prevent="submitPasswordChange">
          <label>
            <span>当前密码</span>
            <input v-model="currentPassword" type="password" autocomplete="current-password" required maxlength="256">
          </label>
          <label>
            <span>新密码</span>
            <input v-model="newPassword" type="password" autocomplete="new-password" required minlength="12" maxlength="256">
          </label>
          <label>
            <span>确认新密码</span>
            <input v-model="confirmPassword" type="password" autocomplete="new-password" required minlength="12" maxlength="256">
          </label>
          <p v-if="errorMessage" class="access-error" role="alert">{{ errorMessage }}</p>
          <button class="button button--primary access-submit" type="submit" :disabled="busy">{{ busy ? '正在更新…' : '更新密码' }}</button>
        </form>
      </template>

      <template v-else>
        <p class="eyebrow">SECURE ADMIN ACCESS</p>
        <h1 id="access-title">登录管理后台</h1>
      <p class="access-description">使用管理员账号进入 Casework 控制台。</p>
        <p v-if="notice" class="access-notice" role="status">{{ notice }}</p>
        <form class="access-form" @submit.prevent="submitLogin">
          <label>
            <span>账号</span>
            <input v-model="username" type="text" autocomplete="username" required maxlength="128" autofocus>
          </label>
          <label>
            <span>密码</span>
            <input v-model="password" type="password" autocomplete="current-password" required maxlength="256">
          </label>
          <p v-if="errorMessage" class="access-error" role="alert">{{ errorMessage }}</p>
          <button class="button button--primary access-submit" type="submit" :disabled="busy">{{ busy ? '正在登录…' : '登录管理后台' }}</button>
        </form>
      </template>
      <p class="access-security">受 HTTPS、会话 Cookie 与 CSRF 防护保护</p>
    </section>
  </main>
</template>
