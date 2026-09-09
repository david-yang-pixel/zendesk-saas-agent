import { computed, onMounted, ref } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'auto'

const THEME_KEY = 'zd_admin_theme'

function getSavedTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'light' || saved === 'dark' || saved === 'auto') return saved
  } catch {
    // ignore
  }
  return 'light'
}

function resolveSystemTheme(): 'light' | 'dark' {
  if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

const currentTheme = ref<ThemeMode>(getSavedTheme())
const activeAppearance = ref<'light' | 'dark'>(
  currentTheme.value === 'auto' ? resolveSystemTheme() : currentTheme.value
)

function applyTheme(theme: ThemeMode) {
  currentTheme.value = theme
  const appearance = theme === 'auto' ? resolveSystemTheme() : theme
  activeAppearance.value = appearance
  try {
    localStorage.setItem(THEME_KEY, theme)
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', appearance)
      document.documentElement.style.colorScheme = appearance
    }
  } catch {
    // ignore
  }
}

// Initial apply
if (typeof document !== 'undefined') {
  applyTheme(currentTheme.value)
  if (typeof window !== 'undefined' && window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (currentTheme.value === 'auto') {
        applyTheme('auto')
      }
    })
  }
}

export function useTheme() {
  const theme = computed(() => currentTheme.value)
  const isDark = computed(() => activeAppearance.value === 'dark')

  function toggleTheme() {
    applyTheme(isDark.value ? 'light' : 'dark')
  }

  function setTheme(t: ThemeMode) {
    applyTheme(t)
  }

  return {
    theme,
    isDark,
    toggleTheme,
    setTheme,
  }
}
