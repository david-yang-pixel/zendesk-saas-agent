import { createApp } from 'vue'
import App from './App.vue'
import { ADMIN_API_KEY } from './api/client'
import { createAppRouter } from './router'
import './styles.css'

async function bootstrap() {
  const preview = import.meta.env.DEV && new URLSearchParams(window.location.search).has('preview')
  const app = createApp(App, { skipAuthentication: preview })
  if (preview) {
    const { previewAdminApi } = await import('./preview-api')
    app.provide(ADMIN_API_KEY, previewAdminApi)
  }
  app.use(createAppRouter()).mount('#app')
}

void bootstrap()
