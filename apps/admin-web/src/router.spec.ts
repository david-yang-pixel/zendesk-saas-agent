import { createWebHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'
import { createAppRouter } from './router'

describe('admin router', () => {
  it('keeps the SPA history under an /admin base path', () => {
    const router = createAppRouter(createWebHistory('/admin/'))

    expect(router.resolve('/harness').href).toBe('/admin/harness')
  })
})
