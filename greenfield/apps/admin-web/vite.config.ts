import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

type ProcessLike = { env?: Record<string, string | undefined> }

export default defineConfig(({ mode }) => {
  const fileEnv = loadEnv(mode, '', '')
  const processEnv = (globalThis as typeof globalThis & { process?: ProcessLike }).process?.env
  const configuredBasePath = processEnv?.ADMIN_WEB_BASE_PATH ?? fileEnv.ADMIN_WEB_BASE_PATH

  return {
    // Production is served behind the `/admin/` reverse-proxy location. Keep
    // Vite dev/test at `/` so the local console remains directly reachable.
    base: configuredBasePath || (mode === 'production' ? '/admin/' : '/'),
    plugins: [vue()],
    test: {
      environment: 'jsdom',
      globals: true,
    },
  }
})
