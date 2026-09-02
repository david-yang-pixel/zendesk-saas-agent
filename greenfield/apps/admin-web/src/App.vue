<script setup lang="ts">
import { computed, inject, onMounted, provide, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from './components/AppIcon.vue'
import AdminAccess from './components/AdminAccess.vue'
import { ADMIN_API_KEY, createAdminApiClient, type AdminSession } from './api/client'
import { navItems } from './navigation'

const props = withDefaults(defineProps<{ skipAuthentication?: boolean }>(), { skipAuthentication: false })
const route = useRoute()
const currentLabel = computed(() => String(route.meta.label ?? '运行总览'))
const api = inject(ADMIN_API_KEY, createAdminApiClient())
provide(ADMIN_API_KEY, api)
const authState = ref<'loading' | 'signed-out' | 'signed-in'>('loading')
const session = ref<AdminSession | null>(null)
const authNotice = ref('')

function authenticated(value: AdminSession) {
  session.value = value
  authState.value = 'signed-in'
  authNotice.value = ''
}

async function signOut() {
  try {
    await api.logout()
  } finally {
    session.value = null
    authState.value = 'signed-out'
    authNotice.value = '已安全退出管理后台。'
  }
}

function passwordChanged() {
  session.value = null
  authState.value = 'signed-out'
  authNotice.value = '密码已更新，请使用新密码重新登录。'
}

onMounted(async () => {
  if (props.skipAuthentication) {
    authenticated({ username: 'test-admin', csrfToken: 'test-csrf', forcePasswordChange: false })
    return
  }
  try {
    authenticated(await api.getSession())
  } catch {
    authState.value = 'signed-out'
  }
})
</script>

<template>
  <main v-if="authState === 'loading'" class="access-page" aria-busy="true" aria-label="正在验证管理会话">
    <div class="access-loading"><span class="pulse-dot" aria-hidden="true" />正在验证安全会话…</div>
  </main>
  <AdminAccess
    v-else-if="authState === 'signed-out' || session?.forcePasswordChange"
    :api="api"
    :session="session"
    :notice="authNotice"
    @authenticated="authenticated"
    @password-changed="passwordChanged"
  />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand-lockup">
        <div class="brand-mark" aria-hidden="true"><span>c</span></div>
        <div>
          <p class="brand-name">Casework</p>
          <p class="brand-caption">OPERATIONS CONSOLE</p>
        </div>
      </div>

      <div class="workspace-switcher" aria-label="当前工作区">
        <div class="workspace-avatar">DS</div>
        <div class="workspace-copy">
          <span class="workspace-label">工作区</span>
          <strong>Dify SaaS Support</strong>
        </div>
        <AppIcon name="chevron" :size="15" />
      </div>

      <p class="nav-section-label">控制台</p>
      <nav class="primary-nav" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :data-nav="item.path === '/' ? 'overview' : item.path.slice(1)"
        >
          <span class="nav-item__icon"><AppIcon :name="item.icon" :size="18" /></span>
          <span class="nav-item__copy">
            <span>{{ item.label }}</span>
            <small>{{ item.hint }}</small>
          </span>
          <span v-if="item.path === '/runs'" class="nav-count">7</span>
        </RouterLink>
      </nav>

      <div class="sidebar-spacer" />
      <div class="sidebar-status">
        <span class="pulse-dot" aria-hidden="true" />
        <div>
          <strong>系统运行正常</strong>
          <span>所有服务已连接</span>
        </div>
      </div>
      <div class="sidebar-footer">
        <div class="avatar">YL</div>
        <div class="user-copy"><strong>{{ session?.username }}</strong><span>Administrator</span></div>
        <button type="button" class="icon-button icon-button--muted" aria-label="退出登录" @click="signOut">
          <AppIcon name="logout" :size="17" />
        </button>
      </div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div class="breadcrumbs" aria-label="面包屑">
          <span>Casework</span><span class="breadcrumb-divider">/</span><strong>{{ currentLabel }}</strong>
        </div>
        <div class="topbar-actions">
          <span class="environment-chip"><span class="pulse-dot" aria-hidden="true" /> Production</span>
          <button type="button" class="icon-button" aria-label="搜索">
            <AppIcon name="search" :size="18" />
          </button>
          <button type="button" class="icon-button notification-button" aria-label="通知">
            <AppIcon name="bell" :size="18" /><span class="notification-dot" aria-hidden="true" />
          </button>
          <div class="topbar-divider" />
          <span class="revision-label">Config <strong>r42</strong></span>
        </div>
      </header>

      <div class="page-container">
        <RouterView />
      </div>
    </main>
  </div>
</template>
