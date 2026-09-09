<script setup lang="ts">
import { computed } from 'vue'
import type { IconName } from '../types'

const props = withDefaults(defineProps<{ name: IconName; size?: number }>(), { size: 18 })

const iconPaths: Record<IconName, string[]> = {
  overview: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
  harness: ['M4 6h16', 'M4 12h16', 'M4 18h16', 'M8 4v4', 'M16 10v4', 'M10 16v4'],
  rules: ['M4 5h16', 'M7 12h10', 'M10 19h4', 'M6 5l4 7v7h4v-7l4-7'],
  skills: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z', 'M19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z'],
  runs: ['M4 17l4-5 4 3 5-8 3 4', 'M4 20h16'],
  chevron: ['M8 10l4 4 4-4'],
  external: ['M14 4h6v6', 'M20 4l-9 9', 'M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6'],
  bell: ['M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9', 'M10 21h4'],
  search: ['m21 21-4.35-4.35', 'M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z'],
  refresh: ['M20 11a8 8 0 0 0-14.9-4L3 10', 'M3 4v6h6', 'M4 13a8 8 0 0 0 14.9 4L21 14', 'M21 20v-6h-6'],
  play: ['M8 5v14l11-7z'],
  pause: ['M8 5v14', 'M16 5v14'],
  check: ['m5 12 4 4L19 6'],
  alert: ['M12 4 3 20h18L12 4Z', 'M12 10v4', 'M12 17h.01'],
  clock: ['M12 7v5l3 2', 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z'],
  sliders: ['M4 6h16', 'M4 12h16', 'M4 18h16', 'M8 4v4', 'M16 10v4', 'M10 16v4'],
  database: ['M4 5c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2Z', 'M4 5v7c0 1.1 3.6 2 8 2s8-.9 8-2V5', 'M4 12v7c0 1.1 3.6 2 8 2s8-.9 8-2v-7'],
  box: ['M4 7.5 12 3l8 4.5v9L12 21l-8-4.5z', 'M4 7.5l8 4.5 8-4.5', 'M12 12v9'],
  spark: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z', 'M19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z'],
  shield: ['M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', 'm9 12 2 2 4-4'],
  more: ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'],
  'arrow-up': ['M12 19V5', 'm6 11 6-6 6 6'],
  'arrow-down': ['M12 5v14', 'm18 13-6 6-6-6'],
  activity: ['M3 12h4l3-8 4 16 3-8h4'],
  terminal: ['m5 7 4 5-4 5', 'M12 17h7'],
  download: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M7 10l5 5 5-5', 'M12 15V3'],
  logout: ['M10 5H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h5', 'M14 8l4 4-4 4', 'M18 12H9'],
  sun: ['M12 3v2', 'M12 19v2', 'M4.22 4.22l1.42 1.42', 'M18.36 18.36l1.42 1.42', 'M3 12h2', 'M19 12h2', 'M4.22 19.78l1.42-1.42', 'M18.36 5.64l1.42-1.42', 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10z'],
  moon: ['M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z'],
  globe: ['M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z', 'M2 12h20', 'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'],
  sidebar: ['M3 3h18v18H3z', 'M9 3v18'],
}

const paths = computed(() => iconPaths[props.name])
</script>

<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="path in paths" :key="path" :d="path" />
  </svg>
</template>
