<script setup lang="ts">
import { computed, inject, onMounted, provide, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from './components/AppIcon.vue'
import AdminAccess from './components/AdminAccess.vue'
import { ADMIN_API_KEY, createAdminApiClient, type AdminSession } from './api/client'
import { useI18n } from './composables/useI18n'
import { useTheme } from './composables/useTheme'

const props = withDefaults(defineProps<{ skipAuthentication?: boolean }>(), { skipAuthentication: false })
const route = useRoute()
const { locale, setLocale, t } = useI18n()
const { isDark, toggleTheme } = useTheme()

const localizedNavItems = computed(() => [
  { label: t('nav.runs'), path: '/runs', icon: 'runs' as const },
  { label: t('nav.model'), path: '/model', icon: 'spark' as const },
  { label: t('nav.rules'), path: '/rules', icon: 'rules' as const },
  { label: t('nav.tools'), path: '/tools', icon: 'database' as const },
])

const currentLabel = computed(() => {
  const p = route.path
  if (p === '/runs') return t('nav.runs')
  if (p === '/model') return t('nav.model')
  if (p === '/rules') return t('nav.rules')
  if (p === '/tools') return t('nav.tools')
  return String(route.meta.label ?? t('nav.runs'))
})

function getStoredSidebarCollapsed(): boolean {
  try {
    if (typeof localStorage !== 'undefined' && typeof localStorage.getItem === 'function') {
      return localStorage.getItem('zd_sidebar_collapsed') === 'true'
    }
  } catch {}
  return false
}

const isCollapsed = ref(getStoredSidebarCollapsed())

function toggleSidebar() {
  isCollapsed.value = !isCollapsed.value
  try {
    if (typeof localStorage !== 'undefined' && typeof localStorage.setItem === 'function') {
      localStorage.setItem('zd_sidebar_collapsed', String(isCollapsed.value))
    }
  } catch {}
}

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
    <aside :class="['sidebar', { collapsed: isCollapsed }]">
      <div class="brand-lockup">
        <div class="brand-mark" aria-hidden="true" title="Zendesk Agent" @click="toggleSidebar"><span>Z</span></div>
        <div class="brand-meta" v-show="!isCollapsed">
          <span class="brand-name">{{ t('brand.name') }}</span>
          <span class="brand-pill">Admin</span>
        </div>
      </div>

      <nav class="primary-nav" aria-label="主导航">
        <RouterLink
          v-for="item in localizedNavItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :data-nav="item.path.slice(1)"
          :title="item.label"
        >
          <span class="nav-item__icon"><AppIcon :name="item.icon" :size="18" /></span>
          <span class="nav-item__label">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar-spacer" />

      <!-- Sidebar Rail Toggle Button -->
      <div class="sidebar-toggle-row">
        <button
          type="button"
          class="sidebar-toggle-btn"
          :title="isCollapsed ? '展开导航栏' : '收起为极简图标轨'"
          :aria-label="isCollapsed ? '展开导航栏' : '收起为极简图标轨'"
          @click="toggleSidebar"
        >
          <AppIcon name="sidebar" :size="16" />
          <span class="toggle-text" v-show="!isCollapsed">折叠菜单</span>
        </button>
      </div>

      <div class="sidebar-footer">
        <div class="avatar" :title="session?.username">{{ session?.username?.slice(0, 1).toUpperCase() }}</div>
        <div class="user-copy" v-show="!isCollapsed">
          <strong>{{ session?.username }}</strong>
          <span>{{ t('user.admin') }}</span>
        </div>
        <button type="button" class="icon-button icon-button--muted" :aria-label="t('user.logout')" :title="t('user.logout')" @click="signOut">
          <AppIcon name="logout" :size="16" />
        </button>
      </div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div class="topbar-left">
          <button
            type="button"
            class="topbar-collapse-btn"
            :title="isCollapsed ? '展开侧边栏' : '收起为极简图标轨'"
            :aria-label="isCollapsed ? '展开侧边栏' : '收起为极简图标轨'"
            @click="toggleSidebar"
          >
            <AppIcon name="sidebar" :size="16" />
          </button>
          <div class="breadcrumbs" aria-label="当前页面">
            <strong>{{ currentLabel }}</strong>
          </div>
        </div>
        <div class="topbar-actions">
          <span class="environment-chip"><span class="pulse-dot" aria-hidden="true" />{{ t('workspace.env') }}</span>

          <!-- Language Selector Pill -->
          <div class="lang-switch-pill" role="group" aria-label="Language Selector">
            <button
              type="button"
              :class="['lang-btn', { active: locale === 'zh-CN' }]"
              @click="setLocale('zh-CN')"
              title="切换至简体中文"
            >
              {{ t('lang.zh') }}
            </button>
            <button
              type="button"
              :class="['lang-btn', { active: locale === 'en-US' }]"
              @click="setLocale('en-US')"
              title="Switch to English"
            >
              {{ t('lang.en') }}
            </button>
          </div>

          <!-- Theme Toggle Button -->
          <button
            type="button"
            class="theme-toggle-btn"
            :title="isDark ? t('theme.light') : t('theme.dark')"
            @click="toggleTheme"
            aria-label="Toggle Theme"
          >
            <AppIcon :name="isDark ? 'sun' : 'moon'" :size="16" />
          </button>

          <span class="topbar-account">{{ session?.username }}</span>
        </div>
      </header>

      <div class="page-container">
        <RouterView />
      </div>
    </main>
  </div>
</template>
